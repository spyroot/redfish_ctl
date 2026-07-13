"""Contract tests for the Redfish corpus manifest (tests/corpus/manifest.json).

The manifest is the machine-readable index every sim/test/mock and the
``tools/corpus.py`` CLI resolve corpora through (see docs/corpus-library.md).
These tests pin its shape and keep the recorded JSON counts honest against the
actual tarball contents. A tarball that is still a bare Git-LFS pointer (not
``git lfs pull``ed on this machine) is skipped for the count check rather than
failing, so the offline suite stays green on a fresh checkout.
"""
from __future__ import annotations

import subprocess
import tarfile
from pathlib import Path

import pytest

from tools import corpus

REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIRED_KEYS = {
    "vendor", "model", "product", "redfish_version",
    "json_count", "surface", "tarball", "arcname", "source_note",
}


def _lfs_tracked_paths() -> set[str]:
    """Repo-relative paths Git-LFS tracks (empty if git/lfs unavailable)."""
    try:
        out = subprocess.check_output(
            ["git", "lfs", "ls-files", "--name-only"],
            cwd=REPO_ROOT, text=True, stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):  # pragma: no cover - env-dependent
        return set()
    return {line.strip() for line in out.splitlines() if line.strip()}


def test_manifest_parses_and_has_rows():
    """The manifest loads and lists at least the five known corpora."""
    rows = corpus.load_manifest()
    assert len(rows) >= 5
    vendors = {r["vendor"] for r in rows}
    assert {"dell", "hpe", "supermicro", "nvidia"} <= vendors


@pytest.mark.parametrize("row", corpus.load_manifest(), ids=lambda r: f"{r['vendor']}-{r['model']}")
def test_row_shape(row):
    """Every row carries the required keys with sane types."""
    assert REQUIRED_KEYS <= set(row), f"missing keys: {REQUIRED_KEYS - set(row)}"
    assert isinstance(row["json_count"], int) and row["json_count"] > 0
    assert row["tarball"].startswith("tests/") and row["tarball"].endswith(".tar.gz")
    # arcname must be the tarball's real internal root (see count check below).
    assert row["arcname"]


@pytest.mark.parametrize("row", corpus.load_manifest(), ids=lambda r: f"{r['vendor']}-{r['model']}")
def test_tarball_exists_and_is_lfs_tracked(row):
    """Each declared tarball is present on disk and tracked by Git-LFS."""
    path = REPO_ROOT / row["tarball"]
    assert path.exists(), f"{row['tarball']} missing"
    tracked = _lfs_tracked_paths()
    if tracked:  # only assert when git-lfs is queryable in this environment
        assert row["tarball"] in tracked, f"{row['tarball']} not LFS-tracked"


@pytest.mark.parametrize("row", corpus.load_manifest(), ids=lambda r: f"{r['vendor']}-{r['model']}")
def test_json_count_and_root_match(row):
    """json_count and arcname match the tarball (skipped for un-pulled pointers)."""
    path = REPO_ROOT / row["tarball"]
    if corpus._is_lfs_pointer(path):
        pytest.skip(f"{row['tarball']} is a bare LFS pointer; run `git lfs pull`")
    with tarfile.open(path) as tar:
        names = tar.getnames()
    actual = sum(1 for n in names if n.endswith(".json"))
    assert actual == row["json_count"], (
        f"{row['tarball']}: manifest={row['json_count']} actual={actual}")
    roots = {n.split("/", 1)[0] for n in names if n}
    assert row["arcname"] in roots, (
        f"{row['tarball']}: arcname {row['arcname']!r} not the tarball root {roots}")


def test_resolve_by_vendor_model():
    """resolve() maps a vendor/model pair to its row and returns None otherwise."""
    row = corpus.resolve("dell", "xr8620t")
    assert row and row["tarball"] == "tests/dell_xr8620t_corpus.tar.gz"
    assert corpus.resolve("nope", "nope") is None
