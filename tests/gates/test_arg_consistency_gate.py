"""Offline tests for the arg-consistency ratchet gate.

The gate (tools/arg_consistency_gate.py) flags a concept spelled two ways
(--event-type and --event_type) that is not baselined, and flags a baseline
entry that no longer splits. Driven by monkeypatching the flag/baseline sets.

Author Mus spyroot@gmail.com
"""
from tools import arg_consistency_gate as gate


def test_single_spelling_is_clean(monkeypatch, capsys):
    """A concept with one spelling passes."""
    monkeypatch.setattr(gate, "_flags", lambda: {"--reset-type", "--share-name"})
    monkeypatch.setattr(gate, "_baseline", lambda: set())
    assert gate.main() == 0
    assert "clean" in capsys.readouterr().out


def test_dash_underscore_split_flagged(monkeypatch, capsys):
    """The same concept in dash and underscore form is a violation."""
    monkeypatch.setattr(gate, "_flags", lambda: {"--reset-type", "--reset_type"})
    monkeypatch.setattr(gate, "_baseline", lambda: set())
    assert gate.main() == 1
    assert "--reset-type|--reset_type" in capsys.readouterr().out


def test_baselined_split_allowed(monkeypatch, capsys):
    """A grandfathered split passes."""
    monkeypatch.setattr(gate, "_flags", lambda: {"--reset-type", "--reset_type"})
    monkeypatch.setattr(gate, "_baseline", lambda: {"--reset-type|--reset_type"})
    assert gate.main() == 0


def test_fixed_split_leaves_stale_baseline(monkeypatch, capsys):
    """Once a split is fixed (one spelling), its baseline entry is stale and
    the gate demands its removal — the ratchet cannot loosen."""
    monkeypatch.setattr(gate, "_flags", lambda: {"--reset-type"})
    monkeypatch.setattr(gate, "_baseline", lambda: {"--reset-type|--reset_type"})
    assert gate.main() == 1
    assert "stale" in capsys.readouterr().out.lower()


def test_idrac_underscore_not_penalized(monkeypatch, capsys):
    """A single underscore flag (--idrac_ip) is fine — only BOTH spellings of
    one concept violate, not the underscore convention itself."""
    monkeypatch.setattr(gate, "_flags", lambda: {"--idrac_ip", "--idrac_username"})
    monkeypatch.setattr(gate, "_baseline", lambda: set())
    assert gate.main() == 0


def test_real_repo_gate_is_clean():
    """The shipped baseline covers the real repo — main() returns 0."""
    assert gate.main() == 0
