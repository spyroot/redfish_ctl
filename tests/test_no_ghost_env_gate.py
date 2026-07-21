"""Offline tests for the no-ghost-env registry gate.

The gate (tools/no_ghost_env_gate.py) fails on an env var read whose EXACT name
is not declared in specs/config/environment.yaml (a ghost). Driven by
monkeypatching the registry and read sets so the logic is proven without editing
the real registry.

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


def test_standard_prefix_is_not_a_free_pass(monkeypatch, capsys):
    """An OTEL_*/SPLUNK_* name not listed exactly is still a ghost — no prefix
    allowlist lets it through just because the prefix looks standard."""
    monkeypatch.setattr(gate, "_registry", lambda: {"OTEL_EXPORTER_OTLP_ENDPOINT"})
    monkeypatch.setattr(gate, "_read_names", lambda: {"OTEL_SOME_NEW_TIMEOUT"})
    assert gate.main() == 1
    assert "OTEL_SOME_NEW_TIMEOUT" in capsys.readouterr().out


def test_registry_loads_exact_names_from_yaml():
    """The real registry parses and yields exact names; retired names are excluded.

    Retired names are read from the registry (not hardcoded here) so this test
    itself never mentions a tombstoned literal.
    """
    import yaml
    names = gate._registry()
    assert {"REDFISH_IP", "IDRAC_IP", "OTEL_EXPORTER_OTLP_ENDPOINT",
            "SPLUNK_ACCESS_TOKEN", "TERM"} <= names
    reg = yaml.safe_load(gate._REGISTRY.read_text(encoding="utf-8"))
    retired = set((reg.get("retired") or {}).keys())
    assert retired and not (retired & names)  # retired exist, but none is declared


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
