"""Offline tests for the no-legacy-resurrection gate.

The gate (tools/no_legacy_resurrection_gate.py) fails when a retired name
reappears (tombstone) or when app code reads a deprecated IDRAC_* name unpaired
with its canonical. The AST direct-use logic is tested on source snippets; the
tombstone/registry logic via monkeypatched sets.

Author Mus spyroot@gmail.com
"""
import ast

from tools import no_legacy_resurrection_gate as gate

_LEGACY = {"IDRAC_HTTP_TIMEOUT": "REDFISH_HTTP_TIMEOUT", "IDRAC_IP": "REDFISH_IP"}


def _flagged(src: str) -> list[str]:
    """Return legacy names flagged as unpaired uses in a source snippet.

    Mirrors the gate's statement-grouping: a legacy literal is flagged unless its
    canonical appears in the same enclosing statement — regardless of how it is
    read (call, subscript, tuple, f-string).

    :param src: python source to analyze.
    :return: the legacy names flagged.
    """
    tree = ast.parse(src)
    parent = {id(c): p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
    groups: dict[int, dict] = {}
    for node in ast.walk(tree):
        value = gate._literal_str(node)
        if value is None:
            continue
        stmt = gate._enclosing_stmt(node, parent)
        if stmt is None:
            continue
        g = groups.setdefault(id(stmt), {"vals": set(), "hits": []})
        g["vals"].add(value)
        if value in _LEGACY:
            g["hits"].append(value)
    return [n for g in groups.values() for n in g["hits"] if _LEGACY[n] not in g["vals"]]


def test_paired_env_first_is_clean():
    """env_first(REDFISH_X, IDRAC_X) names both, so the legacy use is allowed."""
    assert _flagged('env_first("REDFISH_HTTP_TIMEOUT", "IDRAC_HTTP_TIMEOUT")') == []


def test_or_chain_fallback_is_clean():
    """getenv(REDFISH) or getenv(IDRAC) pairs in one statement — not flagged."""
    assert _flagged('x = os.getenv("REDFISH_IP") or os.getenv("IDRAC_IP")') == []


def test_unpaired_call_read_is_flagged():
    """A bare os.environ.get on the legacy name (no canonical) is flagged."""
    assert _flagged('os.environ.get("IDRAC_HTTP_TIMEOUT")') == ["IDRAC_HTTP_TIMEOUT"]


def test_subscript_read_is_flagged():
    """The idiomatic os.environ["IDRAC_IP"] subscript is caught (regression:
    the Call-only check missed this, the most common direct-read form)."""
    assert _flagged('x = os.environ["IDRAC_IP"]') == ["IDRAC_IP"]


def test_legacy_only_in_container_is_flagged():
    """A legacy name alone in a tuple/list (variable indirection) is caught."""
    assert _flagged('ENVS = ("IDRAC_IP",)') == ["IDRAC_IP"]


def test_paired_container_is_clean():
    """A tuple naming both canonical and legacy is a legitimate pair."""
    assert _flagged('ENVS = ("REDFISH_IP", "IDRAC_IP")') == []


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
