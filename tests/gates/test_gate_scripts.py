"""Execution-level checks for the gate scripts themselves (scripts/gates/**).

The meta-gate (tools/gate_meta.py) validates the registry as DATA — ids, profiles, runner tags,
allow_failure. It never runs a gate, so a registered gate that always exits 0, or a runner that cannot
load the registry at all, passes every existing check. These tests close that gap by executing the
scripts and asserting they fail when they must.

Author Mus spyroot@gmail.com
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY = REPO_ROOT / "gates" / "manifest.yaml"
CHECK_SH = REPO_ROOT / "scripts" / "check.sh"


def _in_a_pod() -> bool:
    """Report whether this test process is itself running inside a Kubernetes pod.

    :return: True when the kubelet's own evidence is present, mirroring check.sh's guard.
    """
    if not (os.environ.get("KUBERNETES_SERVICE_HOST") and os.environ.get("KUBERNETES_SERVICE_PORT")):
        return False
    return Path("/proc/1/cgroup").is_file()


def _registry() -> dict:
    """Load the gate registry.

    :return: the parsed gates/manifest.yaml mapping.
    """
    return yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))


def test_registry_commands_exist_and_are_executable() -> None:
    """Every command named in the registry resolves to an executable file.

    A registry row pointing at a moved or misspelled path is invisible until the profile actually runs
    in CI, where it surfaces as a late pipeline failure instead of a review finding.
    """
    missing = []
    not_executable = []
    for gate in _registry()["gates"]:
        path = REPO_ROOT / gate["command"]
        if not path.is_file():
            missing.append(gate["id"])
        elif not os.access(path, os.X_OK):
            not_executable.append(gate["id"])
    assert not missing, f"registry commands do not exist: {missing}"
    assert not not_executable, f"registry commands are not executable: {not_executable}"


def test_run_sh_loads_the_registry_and_rejects_an_unknown_profile() -> None:
    """run.sh reads the real registry, and an unregistered profile is an error, not a silent pass.

    Two regressions in one: the runner previously read a nonexistent ``gates.yaml`` (so every profile
    died before running a gate), and an unmatched profile exited 0 — meaning a typo in the profile name
    produced a green pipeline that ran nothing. A bogus profile is used deliberately so the test never
    executes real gates.
    """
    proc = subprocess.run(
        [str(REPO_ROOT / "scripts" / "gates" / "run.sh"), "no-such-profile"],
        capture_output=True, text=True,
    )
    assert proc.returncode != 0, "an unknown profile must fail, not pass silently"
    combined = proc.stdout + proc.stderr
    assert "unknown profile" in combined, combined
    # Proves the registry parsed: the error lists the profiles it found rather than a load traceback.
    assert "merge" in combined, combined
    assert "FileNotFoundError" not in combined, combined


def test_evidence_sanitized_fails_on_a_planted_secret(tmp_path) -> None:
    """evidence.sanitized exits non-zero when the evidence dir contains secret-shaped content.

    The gate previously passed an argument-taking grep flag (-D), which consumed the pattern as that
    flag's value; grep exited 2, the ``if`` read false, and the gate printed OK. It could not fail on
    any input, so this asserts the positive detection path directly.
    """
    (tmp_path / "trace.log").write_text("password: hunter2hunter2\n", encoding="utf-8")
    proc = subprocess.run(
        [str(REPO_ROOT / "scripts" / "gates" / "evidence" / "sanitized.sh")],
        capture_output=True, text=True, env={**os.environ, "EVIDENCE_DIR": str(tmp_path)},
    )
    assert proc.returncode == 1, f"planted secret was not detected: {proc.stdout}{proc.stderr}"
    assert "sanitize before upload" in proc.stdout + proc.stderr


def test_evidence_sanitized_passes_on_clean_evidence(tmp_path) -> None:
    """evidence.sanitized exits 0 on an evidence dir with no secret-shaped content.

    Guards the other direction: a gate that fails on everything is as useless as one that never fails.
    """
    (tmp_path / "gate-report.md").write_text("| repo.no-secrets | PASS |\n", encoding="utf-8")
    proc = subprocess.run(
        [str(REPO_ROOT / "scripts" / "gates" / "evidence" / "sanitized.sh")],
        capture_output=True, text=True, env={**os.environ, "EVIDENCE_DIR": str(tmp_path)},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_yaml_gate_fallback_skips_helm_templates(tmp_path) -> None:
    """The Python fallback selects the same non-template YAML as yamllint.

    Helm templates contain Go template expressions rather than parseable raw
    YAML. The primary yamllint path excludes those files, so the fallback must
    preserve that selection while concrete chart output remains covered by the
    required Kubernetes render and schema gates.
    """
    command_dir = tmp_path / "bin"
    command_dir.mkdir()
    commands = {
        "dirname": shutil.which("dirname"),
        "git": shutil.which("git"),
        "python": sys.executable,
    }
    assert all(commands.values()), commands
    for name, target in commands.items():
        (command_dir / name).symlink_to(target)

    script = REPO_ROOT / "scripts" / "gates" / "repository" / "yaml.sh"
    proc = subprocess.run(
        ["/bin/bash", str(script)],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": str(command_dir)},
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, combined
    assert "repo.yaml: OK (python fallback)" in combined


@pytest.mark.skipif(shutil.which("kubeconform") is None, reason="kubeconform not in this environment")
def test_kubernetes_schema_validates_a_non_empty_manifest_set() -> None:
    """kubernetes.schema selects concrete manifests and reports how many it validated.

    The selection previously inverted: ``grep -qL`` silently behaves as ``grep -q`` (the -q wins), so the
    gate validated exactly the Helm templates it meant to skip and zero real manifests. An empty
    selection now fails the gate rather than printing OK, so the count in the output is the assertion.
    """
    proc = subprocess.run(
        [str(REPO_ROOT / "scripts" / "gates" / "kubernetes" / "schema.sh")],
        capture_output=True, text=True,
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, combined
    assert "no concrete manifests selected" not in combined, combined
    assert "kubernetes.schema: OK" in combined, combined


def test_check_sh_refuses_when_only_the_service_host_variable_is_set() -> None:
    """check.sh refuses the profile path when a single Kubernetes variable is exported.

    The guard previously read one variable, so `export KUBERNETES_SERVICE_HOST=...` on a workstation
    ran the entire merge profile on the operator's laptop — the one thing this project forbids
    everywhere. The edge is ordinary: a sourced env file, a devcontainer, or anyone trying to get past
    the refusal sets exactly that variable. A deliberately unregistered profile is used so that if the
    guard ever regresses, run.sh still rejects it before any real gate can execute.
    """
    env = {k: v for k, v in os.environ.items() if k != "KUBERNETES_SERVICE_PORT"}
    env["KUBERNETES_SERVICE_HOST"] = "10.96.0.1"
    proc = subprocess.run(
        [str(CHECK_SH), "--profile", "no-such-profile"],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT),
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 3, f"expected the local refusal (3), got {proc.returncode}: {combined}"
    assert "REFUSING" in combined, combined
    assert "unknown profile" not in combined, f"the guard let the runner start: {combined}"


@pytest.mark.skipif(not _in_a_pod(), reason="in-cluster acceptance: only meaningful inside a pod")
def test_check_sh_still_runs_in_cluster() -> None:
    """check.sh accepts a real pod, so the hardened guard cannot break the in-cluster CI job.

    The guard requires kubelet evidence, and some of that evidence is legitimately absent: a pod with
    automountServiceAccountToken false has no service-account files, and a cgroup-v2 pod with a private
    cgroup namespace reads only "0::/". An over-strict guard would refuse inside k8s/ci/test-job.yaml or
    platform/agent-runner/job.yaml — a self-inflicted CI outage. Reaching the runner's own "unknown
    profile" error proves the guard passed without executing any gate.
    """
    proc = subprocess.run(
        [str(CHECK_SH), "--profile", "no-such-profile"],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    combined = proc.stdout + proc.stderr
    assert "REFUSING" not in combined, f"the guard refused inside a real pod: {combined}"
    assert "unknown profile" in combined, combined


def test_protected_apply_refuses_without_a_protected_pipeline() -> None:
    """mutation.protected-apply refuses an unprotected pipeline with no override variable available.

    The deploy job used to set ALLOW_PROTECTED_APPLY=1 in its own variables while the gate accepted that
    as an alternative to GitLab's CI_COMMIT_REF_PROTECTED — so the only job running the deploy profile
    disabled the check that guards it. Setting the old variable here must no longer help.
    """
    script = str(REPO_ROOT / "scripts" / "gates" / "mutation" / "protected-apply.sh")
    env = {k: v for k, v in os.environ.items() if not k.startswith("CI_")}
    proc = subprocess.run(
        [script], capture_output=True, text=True, env={**env, "ALLOW_PROTECTED_APPLY": "1"},
    )
    assert proc.returncode == 1, "the retired override still bypasses the gate"
    assert "REFUSED" in proc.stdout + proc.stderr


def test_protected_apply_refuses_a_merge_request_pipeline() -> None:
    """mutation.protected-apply refuses a merge-request pipeline even when the ref is protected.

    An MR pipeline can carry a protected ref, so the MR check must be evaluated independently of
    CI_COMMIT_REF_PROTECTED — this is the last control stopping a live apply from a merge request.
    """
    script = str(REPO_ROOT / "scripts" / "gates" / "mutation" / "protected-apply.sh")
    env = {k: v for k, v in os.environ.items() if not k.startswith("CI_")}
    proc = subprocess.run(
        [script], capture_output=True, text=True,
        env={**env, "CI_PIPELINE_SOURCE": "merge_request_event", "CI_COMMIT_REF_PROTECTED": "true"},
    )
    assert proc.returncode == 1, "a live apply must never run from a merge-request pipeline"
    assert "merge-request pipeline" in proc.stdout + proc.stderr
