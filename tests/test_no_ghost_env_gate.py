"""Offline tests for the no-ghost-env registry gate.

The gate (tools/no_ghost_env_gate.py) fails on an env var read that is not in
tools/env_registry.txt (a ghost) and on a registry entry nothing reads (stale).
Driven by monkeypatching the registry and read sets so the logic is proven
without editing the real registry.

Author Mus spyroot@gmail.com
"""
from tools import no_ghost_env_gate as gate


def test_registered_read_is_clean(monkeypatch, capsys):
    """A read whose name is in the registry passes."""
    monkeypatch.setattr(gate, "_registry", lambda: {"REDFISH_IP"})
    monkeypatch.setattr(gate, "_read_names", lambda: {"REDFISH_IP"})
    assert gate.main() == 0
    assert "clean" in capsys.readouterr().out


def test_unregistered_read_is_a_ghost(monkeypatch, capsys):
    """A read of an unregistered var (a ghost) fails and is named."""
    monkeypatch.setattr(gate, "_registry", lambda: {"REDFISH_IP"})
    monkeypatch.setattr(gate, "_read_names", lambda: {"REDFISH_IP", "SOME_NEW_TIMEOUT"})
    assert gate.main() == 1
    assert "SOME_NEW_TIMEOUT" in capsys.readouterr().out


def test_stale_registry_entry_flagged(monkeypatch, capsys):
    """A registered var nothing reads is flagged for removal (keeps it tight)."""
    monkeypatch.setattr(gate, "_registry", lambda: {"REDFISH_IP", "OLD_UNUSED_VAR"})
    monkeypatch.setattr(gate, "_read_names", lambda: {"REDFISH_IP"})
    assert gate.main() == 1
    assert "OLD_UNUSED_VAR" in capsys.readouterr().out


def test_env_first_names_are_read(tmp_path, monkeypatch):
    """Both names in an env_first(...) call are counted as reads."""
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "redfish_ctl").mkdir()
    (tmp_path / "redfish_ctl" / "m.py").write_text(
        'x = env_first("REDFISH_HTTP_TIMEOUT", "IDRAC_HTTP_TIMEOUT", default="30")\n')
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    assert gate._read_names() == {"REDFISH_HTTP_TIMEOUT", "IDRAC_HTTP_TIMEOUT"}


def test_real_repo_registry_covers_reads():
    """The shipped registry covers every read in the real package."""
    assert gate.main() == 0
