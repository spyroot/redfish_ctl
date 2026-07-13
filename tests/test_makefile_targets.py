"""Makefile contract tests for local developer validation targets."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_TARGETS = {
    "bench-concurrency",
    "help",
    "test",
    "lint",
    "typecheck",
    "build",
    "docker-test",
    "docker-image",
    "docs-voice-check",
    "k8s-sandbox",
    "clean",
}


def test_make_help_lists_required_developer_targets() -> None:
    """The default help output should show every supported local workflow."""
    result = subprocess.run(
        ["make", "help"],
        cwd=REPO_ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 0, result.stderr
    for target in sorted(REQUIRED_TARGETS):
        assert target in result.stdout


def test_make_dry_run_uses_expected_safe_local_commands() -> None:
    """Dry-run recipes should route to local checks and never publish artifacts."""
    result = subprocess.run(
        [
            "make",
            "-n",
            "build",
            "bench-concurrency",
            "docker-test",
            "docker-image",
            "docs-voice-check",
            "k8s-sandbox",
        ],
        cwd=REPO_ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 0, result.stderr
    assert "setup.py sdist bdist_wheel" in result.stdout
    assert "concurrency-benchmark.json" in result.stdout
    assert "twine check" in result.stdout
    assert "docker/run-tests.sh" in result.stdout
    assert "docker build" in result.stdout
    assert "\\b(I|me|my|mine|myself)\\b" in result.stdout
    assert "README.md docs/" in result.stdout
    assert "k8s/sandbox" in result.stdout

    makefile_text = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    assert "twine upload" not in makefile_text
    assert "docker push" not in makefile_text


def test_makefile_local_python_targets_use_project_conda_env_by_default() -> None:
    """Python tooling targets should use the checked-in conda environment by default."""
    result = subprocess.run(
        [
            "make",
            "-n",
            "test",
            "lint",
            "typecheck",
            "build",
        ],
        cwd=REPO_ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 0, result.stderr
    assert " run -n redfish_ctl pytest -q" in result.stdout
    assert " run -n redfish_ctl ruff check redfish_ctl tests" in result.stdout
    assert " run -n redfish_ctl mypy redfish_ctl tests" in result.stdout
    assert " run -n redfish_ctl python setup.py sdist bdist_wheel" in result.stdout
    assert " run -n redfish_ctl twine check dist/*" in result.stdout


def test_conda_environment_includes_make_build_tools() -> None:
    """The project environment should include every tool used by local Makefile targets."""
    environment_text = (REPO_ROOT / "environment.yml").read_text(encoding="utf-8")

    assert "  - twine" in environment_text
