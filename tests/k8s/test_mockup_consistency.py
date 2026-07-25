"""End-to-end contract of the DSP2043 mock-consistency checker.

``tools/mockup_consistency_check.py`` extracts the declared ``@odata.id``
surface from the DSP2043 bundle and interrogates a freshly spawned mock
(``k8s/sandbox/mock_bmc_server.py --mockup-dir``). These tests run the checker
exactly as an operator would: a clean bundle profile exits 0 with matches, and
a tampered served tree exits 1 naming the diverged URI. Bundle-backed tests
skip cleanly when the LFS zip is a bare pointer.

Author Mus spyroot@gmail.com
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from dmtf_mockup import is_lfs_pointer, mockup_profile_dir

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "tools" / "mockup_consistency_check.py"
BUNDLE = (
    REPO_ROOT / "spec" / "dmtf" / "redfish" / "2026.1" / "mockups"
    / "DSP2043_2026.1.zip"
)
PROFILE = "public-telemetry"

requires_bundle = pytest.mark.skipif(
    not BUNDLE.exists() or is_lfs_pointer(BUNDLE),
    reason="DSP2043_2026.1.zip is absent or a bare Git-LFS pointer "
    "(fetch with: git lfs pull)",
)


def _run_checker(*args: str) -> subprocess.CompletedProcess:
    """Run the checker CLI against the repo bundle and capture its output.

    :param args: extra command-line arguments appended after ``--bundle``.
    :return: the completed process (stdout JSON report, stderr diagnostics).
    """
    return subprocess.run(
        [sys.executable, str(CHECKER), "--bundle", str(BUNDLE), *args],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def test_help_works_offline() -> None:
    """--help exits 0 with no bundle, server, or network — the tool contract."""
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0
    assert "--profile" in result.stdout


@requires_bundle
def test_public_telemetry_profile_is_consistent() -> None:
    """A clean profile checks out end-to-end: exit 0, matches, zero mismatches."""
    result = _run_checker("--profile", PROFILE)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    (entry,) = report["profiles"]
    assert entry["profile"] == PROFILE
    assert entry["matched"] > 0
    assert entry["mismatched_total"] == 0
    assert entry["mismatched"] == []
    # Every checked URI lands in exactly one bucket.
    assert entry["checked"] == entry["matched"] + entry["absent_declared"]["count"]
    assert report["totals"]["mismatched"] == 0


@requires_bundle
def test_tampered_serve_tree_fails_and_names_the_uri(tmp_path: Path) -> None:
    """A mutated index.json in the SERVED tree exits 1 and reports its URI.

    Tampering only the served copy keeps the bundle-side expectation pristine —
    the same shape as a real server bug that alters or invents data, which is
    the divergence class this checker exists to catch.
    """
    source = mockup_profile_dir(BUNDLE, PROFILE)
    tampered = tmp_path / PROFILE
    shutil.copytree(source, tampered)
    root_doc = json.loads((tampered / "index.json").read_text(encoding="utf-8"))
    root_doc["Name"] = "Tampered Service Root"
    (tampered / "index.json").write_text(json.dumps(root_doc), encoding="utf-8")

    result = _run_checker("--profile", PROFILE, "--serve-dir", str(tampered))
    assert result.returncode == 1, result.stderr
    report = json.loads(result.stdout)
    (entry,) = report["profiles"]
    assert entry["mismatched_total"] > 0
    assert "/redfish/v1" in {item["uri"] for item in entry["mismatched"]}
