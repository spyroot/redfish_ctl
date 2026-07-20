"""Execution-level checks for the toolbox.consumer-smoke gate.

The gate proves the shared toolbox image can still run this project: the image owns the runtime
(conda, git-lfs) and the project owns its dependencies (environment.yml, created at run time). A
gate that only ever reports OK would be worthless, so these tests drive it into each failure branch
and assert it exits non-zero with the right diagnosis.

The distinction under test is the one that matters operationally: a missing RUNTIME tool is a
provider defect and must report BLOCKED, while missing PROJECT tooling is this repo's own pipeline
defect and must fail plainly. Conflating them would send the wrong team after the wrong bug.

Author Mus spyroot@gmail.com
"""
import os
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE = REPO_ROOT / "scripts" / "gates" / "toolbox" / "consumer-smoke.sh"
REGISTRY = REPO_ROOT / "gates" / "manifest.yaml"


def _run(path_dirs: list[str], extra_env: dict | None = None) -> subprocess.CompletedProcess:
    """Run the gate with a controlled PATH.

    :param path_dirs: directories to expose on PATH, in order; everything else is hidden.
    :param extra_env: additional environment overrides applied on top.
    :return: the completed process, with stdout and stderr captured as text.
    """
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join(path_dirs)
    env.pop("CONDA_PREFIX", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["/bin/bash", str(GATE)], cwd=REPO_ROOT, env=env,
        capture_output=True, text=True, timeout=120,
    )


def _stub(tmp_path: Path, names: list[str]) -> str:
    """Build a PATH directory containing executable stubs for the given command names.

    Real tool discovery is `command -v`, so an executable file is enough to satisfy it. This lets a
    test assert on the gate's branching without installing anything.

    :param tmp_path: pytest-provided temporary directory.
    :param names: command names to create as no-op executables.
    :return: the directory path, ready to place on PATH.
    """
    d = tmp_path / "stub-bin"
    d.mkdir(exist_ok=True)
    for n in names:
        f = d / n
        # yq is read by the gate for the environment name, so a bare exit 0 would
        # short-circuit it on "declares no name" instead of the branch under test.
        body = "#!/bin/sh\necho redfish_ctl\n" if n == "yq" else "#!/bin/sh\nexit 0\n"
        f.write_text(body, encoding="utf-8")
        f.chmod(0o755)
    return str(d)


def test_gate_is_registered_and_mandatory() -> None:
    """The gate must be in the registry and in mandatory_ids, or it cannot block a merge."""
    reg = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    ids = [g["id"] for g in reg["gates"]]
    assert "toolbox.consumer-smoke" in ids
    assert "toolbox.consumer-smoke" in reg["mandatory_ids"]
    entry = next(g for g in reg["gates"] if g["id"] == "toolbox.consumer-smoke")
    assert entry["required"] is True
    assert entry["mutates"] is False


def test_gate_command_exists_and_is_executable() -> None:
    """A registered command that is missing or non-executable fails only at run time, too late."""
    assert GATE.is_file()
    assert os.access(GATE, os.X_OK)


def test_missing_runtime_tool_reports_blocked(tmp_path: Path) -> None:
    """A runtime tool absent from the image is a PROVIDER defect: BLOCKED, never a local install.

    This is the edge that matters most. If the gate quietly installed conda, or passed, a broken
    shared image would look healthy and every consumer would drift to its own toolchain.
    """
    # Everything the project needs, but no conda and no git-lfs.
    path = _stub(tmp_path, ["python", "pytest", "ruff", "yq"])
    res = _run([path])
    assert res.returncode != 0
    assert "BLOCKED" in res.stderr
    assert "conda" in res.stderr
    assert "ci-toolbox.md" in res.stderr, "must name the builder protocol that owns the fix"


def test_missing_project_tooling_fails_without_claiming_blocked(tmp_path: Path) -> None:
    """Missing project deps are THIS repo's pipeline defect, so it fails but must not say BLOCKED."""
    # Runtime present; the project environment was never created or activated.
    path = _stub(tmp_path, ["conda", "git-lfs", "yq"])
    res = _run([path])
    assert res.returncode != 0
    combined = res.stdout + res.stderr
    assert "project tooling not on PATH" in combined
    assert "BLOCKED" not in combined, "a consumer-side defect must not be attributed to the provider"


def test_tooling_outside_the_project_environment_is_rejected(tmp_path: Path) -> None:
    """Deps resolving outside CONDA_PREFIX mean they were baked into the image — the anti-pattern.

    Everything is present and a naive gate would pass here; only the provenance check catches it.
    """
    path = _stub(tmp_path, ["conda", "git-lfs", "yq", "python", "pytest", "ruff"])
    # Claim an environment prefix that the stubs demonstrably do not live under.
    res = _run([path], extra_env={"CONDA_PREFIX": str(tmp_path / "not-the-stub-dir")})
    assert res.returncode != 0
    assert "outside the project environment" in res.stderr
