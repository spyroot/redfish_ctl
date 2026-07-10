"""Makefile contract tests for local developer validation targets."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_TARGETS = {
    "help",
    "test",
    "lint",
    "typecheck",
    "build",
    "docker-test",
    "docker-image",
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
        ["make", "-n", "build", "docker-test", "docker-image", "k8s-sandbox"],
        cwd=REPO_ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 0, result.stderr
    assert "setup.py sdist bdist_wheel" in result.stdout
    assert "twine check" in result.stdout
    assert "docker/run-tests.sh" in result.stdout
    assert "docker build" in result.stdout
    assert "k8s/sandbox" in result.stdout

    makefile_text = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    assert "twine upload" not in makefile_text
    assert "docker push" not in makefile_text
