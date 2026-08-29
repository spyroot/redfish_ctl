"""Offline tests for the config-loader ban gate.

The gate (tools/config_loader_gate.py) forbids a raw env read outside the loader
(redfish_ctl/config.py). It is a plain ban with NO baseline: any scattered read
fails, and every read must live in the loader. Driven by monkeypatching the
violation set.

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


def test_read_outside_loader_fails(monkeypatch, capsys):
    """Any read outside the loader fails and is reported with its location.

    There is no baseline, so a single scattered read is enough to fail the gate.
    """
    monkeypatch.setattr(gate, "_violations", lambda: ["redfish_ctl/cmd_x.py:9"])
    assert gate.main() == 1
    assert "cmd_x.py:9" in capsys.readouterr().out


def test_no_scattered_reads_passes(monkeypatch):
    """With no read outside the loader, the gate passes."""
    monkeypatch.setattr(gate, "_violations", lambda: [])
    assert gate.main() == 0


def test_real_repo_gate_is_clean():
    """Every env read lives in the loader, so main() returns 0 on the real repo."""
    assert gate.main() == 0
