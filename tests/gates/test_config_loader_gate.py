"""Offline tests for the config-loader ratchet gate.

The gate (tools/config_loader_gate.py) forbids a raw env read outside the loader
(redfish_ctl/config.py), grandfathers existing reads, and demands a migrated
read leave the baseline. Driven by monkeypatching the violation/baseline sets.

Author Mus spyroot@gmail.com
"""
from tools import config_loader_gate as gate


def test_read_pattern_matches_forms():
    """os.getenv, os.environ[...], .get, and env_first() all count as reads."""
    for src in ("os.getenv('X')", "os.environ['X']", "os.environ.get('X')",
                "env_first('REDFISH_X', 'IDRAC_X')"):
        assert gate._READ.search(src), src


def test_import_line_not_matched():
    """The env_first re-export import is not a read (no call parens)."""
    assert not gate._READ.search("from .config import env_first as env_first")


def test_new_read_outside_loader_fails(monkeypatch, capsys):
    """A read not in the baseline fails and is reported with its location."""
    monkeypatch.setattr(gate, "_violations", lambda: ["redfish_ctl/cmd_x.py:9"])
    monkeypatch.setattr(gate, "_baseline", lambda: set())
    assert gate.main() == 1
    assert "cmd_x.py:9" in capsys.readouterr().out


def test_baselined_read_allowed(monkeypatch):
    """A grandfathered read passes."""
    monkeypatch.setattr(gate, "_violations", lambda: ["redfish_ctl/cmd_x.py:9"])
    monkeypatch.setattr(gate, "_baseline", lambda: {"redfish_ctl/cmd_x.py:9"})
    assert gate.main() == 0


def test_migrated_read_leaves_stale_baseline(monkeypatch, capsys):
    """Once a read is migrated into the loader, its baseline entry is stale and
    the gate demands removal — the ratchet only tightens."""
    monkeypatch.setattr(gate, "_violations", lambda: [])
    monkeypatch.setattr(gate, "_baseline", lambda: {"redfish_ctl/cmd_x.py:9"})
    assert gate.main() == 1
    assert "stale" in capsys.readouterr().out.lower()


def test_real_repo_gate_is_clean():
    """The shipped baseline covers the real repo — main() returns 0."""
    assert gate.main() == 0
