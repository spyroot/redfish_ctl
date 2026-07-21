"""Offline tests for the no-legacy-resurrection gate.

The gate (tools/no_legacy_resurrection_gate.py) fails when a retired name
reappears (tombstone) or when app code reads a deprecated IDRAC_* name unpaired
with its canonical. The AST direct-use logic is tested on source snippets; the
tombstone/registry logic via monkeypatched sets.

Author Mus spyroot@gmail.com
"""
import ast

from tools import no_legacy_resurrection_gate as gate


def _direct(src: str) -> list[str]:
    """Return legacy names flagged as unpaired direct uses in one call.

    :param src: python source with a single call expression of interest.
    :return: the lines flagged (via the shared call-arg check).
    """
    legacy = {"IDRAC_HTTP_TIMEOUT": "REDFISH_HTTP_TIMEOUT"}
    flagged = []
    for call in ast.walk(ast.parse(src)):
        if isinstance(call, ast.Call):
            args = gate._call_string_args(call)
            for name in args & legacy.keys():
                if legacy[name] not in args:
                    flagged.append(name)
    return flagged


def test_paired_env_first_is_clean():
    """env_first(REDFISH_X, IDRAC_X) names both, so the legacy use is allowed."""
    assert _direct('env_first("REDFISH_HTTP_TIMEOUT", "IDRAC_HTTP_TIMEOUT")') == []


def test_unpaired_direct_read_is_flagged():
    """A bare os.environ.get on the legacy name (no canonical) is flagged."""
    assert _direct('os.environ.get("IDRAC_HTTP_TIMEOUT")') == ["IDRAC_HTTP_TIMEOUT"]


def test_tombstone_hit_fails(monkeypatch, capsys):
    """A retired name found anywhere fails the gate.

    A placeholder name is used so this test never mentions a real tombstoned
    literal (which the gate would otherwise flag in this very file).
    """
    monkeypatch.setattr(gate, "_registry", lambda: {"retired": {"RETIRED_EXAMPLE": {}}})
    monkeypatch.setattr(gate, "_tombstone_hits", lambda reg: ["RETIRED_EXAMPLE @ redfish_ctl/x.py:9"])
    monkeypatch.setattr(gate, "_direct_legacy_uses", lambda m: [])
    monkeypatch.setattr(gate, "_legacy_map", lambda reg: {})
    assert gate.main() == 1
    assert "TOMBSTONE" in capsys.readouterr().out


def test_new_direct_use_fails(monkeypatch, capsys):
    """A direct legacy use not in the baseline fails."""
    monkeypatch.setattr(gate, "_registry", lambda: {})
    monkeypatch.setattr(gate, "_tombstone_hits", lambda reg: [])
    monkeypatch.setattr(gate, "_legacy_map", lambda reg: {})
    monkeypatch.setattr(gate, "_direct_legacy_uses", lambda m: ["redfish_ctl/new.py:5"])
    monkeypatch.setattr(gate, "_baseline", lambda: set())
    assert gate.main() == 1
    assert "LEGACY_ENV_DIRECT_USE" in capsys.readouterr().out


def test_real_repo_gate_is_clean():
    """The shipped registry + baseline leave the real repo clean."""
    assert gate.main() == 0
