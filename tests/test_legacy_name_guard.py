"""Regression tests for the legacy public-name guard."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_legacy_name_guard_passes_repository_tree():
    """The repository keeps old generic idrac names out of public surfaces."""
    result = subprocess.run(
        [sys.executable, "tools/check_legacy_names.py"],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    assert "legacy name guard passed" in result.stdout


def test_legacy_name_guard_detects_forbidden_identifier(tmp_path):
    """The guard fails on generic legacy package identifiers."""
    candidate = tmp_path / "bad.py"
    candidate.write_text("from redfish_ctl.idrac_manager import IDracManager\n")

    result = subprocess.run(
        [sys.executable, "tools/check_legacy_names.py", "--root", str(tmp_path)],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 1
    assert "idrac_manager" in result.stdout
    assert "IDracManager" in result.stdout
