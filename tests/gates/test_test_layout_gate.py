"""Unit tests for the test-layout gate (tools/test_layout_gate.py).

The gate enforces the structure mirror: a test lives in the tests/<domain>/
directory matching its redfish_ctl/<domain>/ subject, and only grandfathered
root-module/infra tests stay flat. These tests pin the flat-file detection and
the ratchet semantics so the layout cannot silently regress to a flat pile.

Author Mus spyroot@gmail.com
"""
import importlib.util
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "test_layout_gate", REPO_ROOT / "tools" / "test_layout_gate.py")
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


def test_flat_detection_counts_only_direct_children():
    """Only files directly under tests/ count as flat; domain-dir files never do."""
    flat = gate._flat_tests()
    assert all(f.startswith("tests/") and f.count("/") == 1 for f in flat)
    assert not any(f.startswith("tests/gates/") for f in flat)


def test_current_tree_is_clean_against_the_baseline():
    """The committed tree passes its own gate (no new flat, no stale entries).

    This is the same invariant CI enforces via the gate wrapper; failing here
    means a test was added flat (move it to its domain dir) or was moved
    without pruning the baseline (delete its line — the ratchet tightens).
    """
    assert set(gate._flat_tests()) == gate._baseline()


def test_baseline_skips_comments_and_blanks():
    """Baseline parsing ignores comment and blank lines."""
    base = gate._baseline()
    assert base, "baseline unexpectedly empty"
    assert all(not entry.startswith("#") for entry in base)
    assert all(entry.startswith("tests/") for entry in base)
