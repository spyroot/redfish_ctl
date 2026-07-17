"""Meta-gate: enforce that the gate registry and pipeline stay internally consistent.

Reads ``gates.yaml`` (the single registry of every mandatory gate) and fails the
build when the pipeline could silently skip, misroute, or mis-classify a gate.
It is itself registered as the ``repo.meta`` gate and is run in CI via
``tests/test_gate_meta.py``. Checks (a check whose inputs do not exist yet — no
``.gitlab-ci.yml``, no ``modules/`` — is reported as skipped, not failed):

    1. every required gate's command file exists
    2. every required gate's command file is executable
    3. every mandatory gate ID is present in the registry
    4. no GitLab job uses ``allow_failure: true``
    5. every GitLab CI job carries the ``homelab-k8s`` runner tag
    6. every module exposes validate/plan/apply/verify/rollback
    7. any module (or gate) that applies also has verify AND rollback
    8. no live-apply (``mutates: true``) job is reachable in a merge-request pipeline

    python tools/gate_meta.py            # exit 0 = consistent, 1 = a check failed
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE = ("validate", "plan", "apply", "verify", "rollback")


def _load_registry() -> dict:
    """Load and minimally validate ``gates.yaml``.

    :return: the parsed registry mapping.
    :raises ValueError: when the file is missing or not a mapping with ``gates``.
    """
    import yaml

    path = REPO_ROOT / "gates.yaml"
    if not path.is_file():
        raise ValueError("gates.yaml is missing")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("gates"), list):
        raise ValueError("gates.yaml must be a mapping with a 'gates' list")
    return data


def _check_commands(registry: dict) -> list[str]:
    """Checks 1 & 2: required gate commands exist and are executable.

    :param registry: the parsed gate registry.
    :return: list of failure messages.
    """
    failures: list[str] = []
    for gate in registry["gates"]:
        if not gate.get("required", False):
            continue
        gate_id = gate.get("id", "<no-id>")
        command = gate.get("command")
        if not command:
            failures.append(f"gate {gate_id}: no command")
            continue
        path = REPO_ROOT / command
        if not path.is_file():
            failures.append(f"gate {gate_id}: command does not exist: {command}")
        elif not (path.stat().st_mode & 0o111):
            failures.append(f"gate {gate_id}: command is not executable: {command}")
    return failures


def _check_mandatory_ids(registry: dict) -> list[str]:
    """Check 3: every mandatory ID appears in the registry.

    :param registry: the parsed gate registry.
    :return: list of failure messages.
    """
    present = {g.get("id") for g in registry["gates"]}
    return [f"mandatory gate ID absent from registry: {mid}"
            for mid in registry.get("mandatory_ids", []) if mid not in present]


def _check_modules() -> tuple[list[str], bool]:
    """Checks 6 & 7: modules expose the full lifecycle; apply implies verify+rollback.

    :return: (failures, ran) — ran is False when there is no ``modules/`` tree.
    """
    modules_dir = REPO_ROOT / "modules"
    if not modules_dir.is_dir():
        return [], False
    failures: list[str] = []
    for module in sorted(p for p in modules_dir.iterdir() if p.is_dir()):
        scripts = module / "scripts"
        have = {name for name in LIFECYCLE if (scripts / f"{name}.sh").is_file()}
        missing = [n for n in LIFECYCLE if n not in have]
        if missing:
            failures.append(f"module {module.name}: missing lifecycle scripts: {missing}")
        elif "apply" in have and not {"verify", "rollback"} <= have:
            failures.append(f"module {module.name}: apply without verify+rollback")
    return failures, True


def _check_gitlab(registry: dict) -> tuple[list[str], bool]:
    """Checks 4, 5 & 8 against ``.gitlab-ci.yml`` when it exists.

    :param registry: the parsed gate registry (for the required runner tag).
    :return: (failures, ran) — ran is False when there is no ``.gitlab-ci.yml``.
    """
    import yaml

    path = REPO_ROOT / ".gitlab-ci.yml"
    if not path.is_file():
        return [], False
    ci = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    runner_tag = registry.get("runner_tag", "homelab-k8s")
    failures: list[str] = []
    for name, job in ci.items():
        if not isinstance(job, dict) or name.startswith(".") or "script" not in job:
            continue  # not a real job (global keys, templates, hidden jobs)
        if job.get("allow_failure") is True:
            failures.append(f"gitlab job {name}: allow_failure:true is forbidden")
        if runner_tag not in (job.get("tags") or []):
            failures.append(f"gitlab job {name}: missing runner tag '{runner_tag}'")
        # Check 8: a live-apply/deploy job must not run in an MR pipeline.
        text = repr(job.get("rules")) + repr(job.get("only"))
        looks_apply = ("apply" in name or "deploy" in name or job.get("mutates") is True)
        if looks_apply and "merge_request" in text:
            failures.append(f"gitlab job {name}: live-apply reachable in a merge-request pipeline")
    return failures, True


def run() -> tuple[bool, list[str], list[str]]:
    """Run every meta-gate check.

    :return: (ok, failures, skipped) — ok is True when there are no failures.
    """
    registry = _load_registry()
    failures = _check_commands(registry) + _check_mandatory_ids(registry)
    skipped: list[str] = []
    mod_fail, mod_ran = _check_modules()
    failures += mod_fail
    if not mod_ran:
        skipped.append("modules/ (no module tree yet)")
    gl_fail, gl_ran = _check_gitlab(registry)
    failures += gl_fail
    if not gl_ran:
        skipped.append(".gitlab-ci.yml (not present yet)")
    return (not failures, failures, skipped)


def main() -> int:
    """CLI entry: print the report and return the process exit code.

    :return: 0 when consistent, 1 when a check failed.
    """
    ok, failures, skipped = run()
    for message in failures:
        print(f"META-GATE FAIL: {message}", file=sys.stderr)
    for note in skipped:
        print(f"meta-gate: skipped {note}")
    print("meta-gate: OK" if ok else f"meta-gate: {len(failures)} failure(s)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
