"""Unit tests for the rename-tombstone gate (tools/rename_tombstone_gate.py).

The gate keeps the renamed-away CLI name (``idrac_ctl`` -> ``redfish_ctl``)
from resurfacing outside the baselined compat-alias surface, and forces docs
cleanups to shrink the baseline. These tests pin the counting, the exemption
of the gate's own files, and the tree-vs-baseline consistency CI relies on.

Author Mus spyroot@gmail.com
"""
import importlib.util
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "rename_tombstone_gate", REPO_ROOT / "tools" / "rename_tombstone_gate.py")
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


def test_occurrences_exclude_the_gate_and_its_baseline():
    """The gate's own files never count — explaining the rule is not a hit."""
    found = gate._occurrences()
    assert "tools/rename_tombstone_gate.py" not in found
    assert "tools/rename_tombstone_baseline.txt" not in found


def test_occurrences_are_positive_counts_of_tracked_files():
    """Every reported row is a tracked file with a positive mention count."""
    found = gate._occurrences()
    assert found, "the alias surface exists, so the scan cannot be empty"
    for path, n in found.items():
        assert n > 0
        assert not path.startswith("/"), "paths are repo-relative"


def test_current_tree_matches_the_baseline_exactly():
    """The committed tree passes its own gate: no new, no grown, no stale.

    This is the invariant CI enforces via the wrapper; a failure here means
    either a resurrected mention (use redfish_ctl) or a cleanup that removed
    mentions without shrinking its baseline row (tighten the ratchet).
    """
    assert gate._occurrences() == gate._baseline()


def test_baseline_parses_paths_and_counts():
    """Baseline rows parse as path:count with comments and blanks ignored."""
    base = gate._baseline()
    assert base, "baseline unexpectedly empty"
    for path, n in base.items():
        assert isinstance(n, int) and n > 0
        assert not path.startswith("#")
