"""Gate: retired names stay dead and connection aliases stay contained.

Three checks enforce the canonical configuration boundary:

1. TOMBSTONE — a name in ``retired`` must never reappear anywhere in the tree
   (read, exported, registered, aliased, or used by a wrapper script).
2. LEGACY_ENV_DIRECT_USE — application code (redfish_ctl/) must not read a
   deprecated ``IDRAC_*`` name directly. A legacy name may appear only in a call
   that also names its canonical ``REDFISH_*`` (the ``env_first(REDFISH_X,
   IDRAC_X)`` pair), so the canonical always takes precedence.
3. RUNTIME_CONNECTION_ALIAS — application code must use the canonical
   ``REDFISH_*`` environment variables and
   ``host``/``username``/``password``/``port`` runtime names.

    python3 tools/no_legacy_resurrection_gate.py

The generic legacy-environment scan remains application-scoped because tests
exercise those compatibility aliases. The connection-name scan also covers
tests and docs. ``tests/conftest.py`` and ``.github/**`` remain outside that
scan until their endpoint contracts are migrated. Existing direct uses are
grandfathered in tools/no_legacy_resurrection_baseline.txt; a new one fails.

Author Mus spyroot@gmail.com
"""
from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

import yaml

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_REGISTRY = _ROOT / "specs" / "config" / "environment.yaml"
_BASELINE = pathlib.Path(__file__).parent / "no_legacy_resurrection_baseline.txt"


def _registry() -> dict:
    """Load the environment registry.

    :return: the parsed registry mapping.
    """
    return yaml.safe_load(_REGISTRY.read_text(encoding="utf-8"))


def _legacy_map(reg: dict) -> dict[str, str]:
    """Return legacy IDRAC_* name -> canonical REDFISH_* name.

    :param reg: the parsed registry.
    :return: mapping of each deprecated alias to its canonical name.
    """
    return {name: meta["canonical"]
            for name, meta in (reg.get("application", {}).get("legacy_aliases") or {}).items()}


def _tombstone_hits(reg: dict) -> list[str]:
    """Return ``name @ file:line`` for any retired name found in the tree.

    :param reg: the parsed registry.
    :return: sorted occurrences of a retired name in tracked files.
    """
    retired = list((reg.get("retired") or {}).keys())
    if not retired:
        return []
    hits: list[str] = []
    for name in retired:
        # git grep the exact token; the registry file itself is the one allowed home.
        out = subprocess.run(
            ["git", "grep", "-nI", "-w", name],
            cwd=_ROOT, capture_output=True, text=True).stdout
        for line in out.splitlines():
            path = line.split(":", 1)[0]
            if path == "specs/config/environment.yaml":
                continue
            loc = ":".join(line.split(":", 2)[:2])
            hits.append(f"{name} @ {loc}")
    return sorted(hits)


def _literal_str(node: ast.AST) -> str | None:
    """Return the string a node denotes, if it is a plain string constant.

    :param node: any AST node.
    :return: the string value, or None if the node is not a string constant.
    """
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _enclosing_stmt(node: ast.AST, parent: dict) -> ast.AST | None:
    """Return the innermost statement enclosing ``node``.

    :param node: the AST node to locate.
    :param parent: child-id -> parent-node map for the tree.
    :return: the nearest ast.stmt ancestor, or None.
    """
    while node is not None and not isinstance(node, ast.stmt):
        node = parent.get(id(node))
    return node


def _direct_legacy_uses(legacy: dict[str, str]) -> list[str]:
    """Return ``path:line`` where app code names a legacy variable unpaired.

    Every string literal is grouped by its innermost enclosing statement. A
    legacy ``IDRAC_*`` literal is flagged unless its canonical ``REDFISH_*``
    appears in the SAME statement — so it is caught however it is read
    (an ``os.environ`` subscript, ``os.getenv`` call, a name
    in a tuple/dict/list, or an f-string) while a legitimate pair
    (``env_first(REDFISH, IDRAC)`` or ``getenv(REDFISH) or getenv(IDRAC)``) is not.

    :param legacy: mapping of legacy name -> canonical name.
    :return: sorted ``"path:line"`` violations in redfish_ctl/.
    """
    out: list[str] = []
    files = subprocess.check_output(
        ["git", "ls-files", "redfish_ctl/*.py", "redfish_ctl/**/*.py"],
        cwd=_ROOT).decode().split()
    for f in files:
        tree = ast.parse((_ROOT / f).read_text(encoding="utf-8"))
        parent = {id(c): p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
        groups: dict[int, dict] = {}
        for node in ast.walk(tree):
            value = _literal_str(node)
            if value is None:
                continue
            stmt = _enclosing_stmt(node, parent)
            if stmt is None:
                continue
            g = groups.setdefault(id(stmt), {"vals": set(), "hits": []})
            g["vals"].add(value)
            if value in legacy:
                g["hits"].append((value, node.lineno))
        for g in groups.values():
            for name, lineno in g["hits"]:
                if legacy[name] not in g["vals"]:
                    out.append(f"{f}:{lineno}")
    return sorted(set(out))


def _runtime_connection_aliases() -> list[str]:
    """Return audited project locations using retired connection names.

    :return: sorted ``"path:line"`` violations in runtime, tests, and docs.
    """
    pattern = (
        r"(^|[^A-Za-z0-9_])"
        r"[iI][dD][rR][aA][cC]_"
        r"(ip|IP|username|USERNAME|password|PASSWORD|port|PORT)"
        r"([^A-Za-z0-9_]|$)"
    )
    proc = subprocess.run(
        [
            "git", "grep", "-nIE", pattern, "--",
            "redfish_ctl", "tests", "tools", "specs", "docs",
            "README.md", "docker", "k8s", "pyproject.toml",
            ":(exclude)tests/conftest.py",
        ],
        cwd=_ROOT,
        capture_output=True,
        text=True,
    )
    return sorted(
        ":".join(line.split(":", 2)[:2])
        for line in proc.stdout.splitlines()
    )


def _baseline() -> set[str]:
    """Return grandfathered direct-use locations.

    :return: the allowed pre-existing ``"path:line"`` entries.
    """
    if not _BASELINE.exists():
        return set()
    return {ln.strip() for ln in _BASELINE.read_text().splitlines()
            if ln.strip() and not ln.startswith("#")}


def main() -> int:
    """Run all no-legacy-resurrection checks.

    :return: 0 when clean, 1 on any tombstone hit, new direct use, or stale entry.
    """
    reg = _registry()
    tombs = _tombstone_hits(reg)
    base = _baseline()
    direct = set(_direct_legacy_uses(_legacy_map(reg)))
    runtime_aliases = _runtime_connection_aliases()
    new = sorted(direct - base)
    stale = sorted(base - direct)
    for t in tombs:
        print(f"TOMBSTONE: {t} — retired name must never reappear (registry: retired)")
    for v in new:
        print(f"LEGACY_ENV_DIRECT_USE: {v} — reads a deprecated IDRAC_* name directly; "
              "use env_first(REDFISH_*, IDRAC_*) so the canonical wins")
    for v in stale:
        print(f"legacy: {v} baselined but no longer a direct use — remove it from the baseline")
    for v in runtime_aliases:
        print(
            f"RUNTIME_CONNECTION_ALIAS: {v} — use canonical connection names"
        )
    if tombs or new or stale or runtime_aliases:
        print(
            "no-legacy-resurrection: "
            f"{len(tombs)} tombstone, {len(new)} new, {len(stale)} stale, "
            f"{len(runtime_aliases)} runtime alias"
        )
        return 1
    print(f"no-legacy-resurrection: clean ({len(base)} direct use(s) baselined)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
