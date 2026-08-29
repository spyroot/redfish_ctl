"""Gate: exception types are defined in ONE place — the exception interface.

Application code must raise the exceptions defined in ``redfish_ctl/cmd_exceptions.py``
and ``redfish_ctl/redfish_exceptions.py`` — the API exception interface — not invent an
ad-hoc exception class at a call site. A new exception class anywhere else fragments the
error contract that the single top-level handler maps to exit codes, and it is invisible
to callers who import the interface. This is the same disease as a scattered env read or
a duplicate connection name: a cross-cutting concern smeared across files instead of
owned in one spot.

    python3 tools/exception_interface_gate.py

Ratchet: existing out-of-interface exception classes are grandfathered in
tools/exception_interface_baseline.txt; a NEW exception class outside the interface
fails the gate, and a migrated one must leave the baseline. The goal is an empty
baseline — every exception type in the interface, none anywhere else.

Author Mus spyroot@gmail.com
"""
from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

# The exception interface — the only modules allowed to define exception types.
_INTERFACE = {
    "redfish_ctl/cmd_exceptions.py",
    "redfish_ctl/redfish_exceptions.py",
}
_BASELINE = pathlib.Path(__file__).parent / "exception_interface_baseline.txt"


def _base_name(base: ast.expr) -> str:
    """Return a class base's simple name (``requests.HTTPError`` -> ``HTTPError``).

    :param base: a base-class expression from a ``ClassDef``.
    :return: the base's final name component, or "" when it is not a plain name.
    """
    return base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", "")


def _suffix_exc(name: str) -> bool:
    """Return whether a name looks like an exception type by suffix.

    Covers builtin and third-party exceptions (``RuntimeError``, ``requests.HTTPError``).

    :param name: a class or base name.
    :return: True when it ends in ``Error`` or ``Exception``.
    """
    return name.endswith("Error") or name.endswith("Exception")


def _all_classes() -> list[tuple[str, int, str, list[str]]]:
    """Return every ``ClassDef`` in ``redfish_ctl/`` as (path, line, name, base names).

    Includes the interface modules (their exception names seed the detection) and
    tolerates an unparseable file: a syntax error is a different gate's job, so it must
    not crash this required gate.

    :return: one tuple per class definition across the package.
    """
    out: list[tuple[str, int, str, list[str]]] = []
    files = subprocess.check_output(
        ["git", "ls-files", "redfish_ctl/*.py", "redfish_ctl/**/*.py"]).decode().split()
    for f in files:
        try:
            tree = ast.parse(pathlib.Path(f).read_text(encoding="utf-8"))
        except (SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                out.append((f, node.lineno, node.name,
                            [_base_name(b) for b in node.bases]))
    return out


def _exception_class_names(classes: list[tuple[str, int, str, list[str]]]) -> set[str]:
    """Return, transitively, the name of every class that is an exception type.

    A class is an exception when a base is suffixed ``Error``/``Exception`` OR inherits
    from an already-known exception class. The fixpoint closes the bypass of subclassing
    a project exception whose own name is not suffixed (e.g. ``ConfigurationConflict``).

    :param classes: all class definitions in the package.
    :return: the set of exception class names.
    """
    known: set[str] = set()
    changed = True
    while changed:
        changed = False
        for _f, _line, name, bases in classes:
            if name in known:
                continue
            if any(_suffix_exc(b) or b in known for b in bases):
                known.add(name)
                changed = True
    return known


def _violations() -> list[str]:
    """Return ``path:line`` for every exception class defined outside the interface.

    :return: sorted ``"path:line"`` strings, one per offending class definition.
    """
    classes = _all_classes()
    exception_names = _exception_class_names(classes)
    return sorted(
        f"{f}:{line}"
        for f, line, name, _bases in classes
        if f not in _INTERFACE and name in exception_names
    )


def _baseline() -> set[str]:
    """Return grandfathered ``path:line`` exception-class locations.

    :return: the allowed pre-existing offending locations.
    """
    if not _BASELINE.exists():
        return set()
    return {ln.strip() for ln in _BASELINE.read_text().splitlines()
            if ln.strip() and not ln.startswith("#")}


def main() -> int:
    """Report new out-of-interface exception classes and stale baseline entries.

    :return: 0 when clean, 1 on a new exception class or a stale baseline entry.
    """
    base = _baseline()
    viol = set(_violations())
    new = sorted(viol - base)
    stale = sorted(base - viol)
    for v in new:
        print(f"exception-interface: {v} — exception class defined outside the interface; "
              "define it in redfish_ctl/cmd_exceptions.py or redfish_ctl/redfish_exceptions.py")
    for v in stale:
        print(f"exception-interface: {v} baselined but no longer an exception class — "
              "remove it from the baseline (ratchet tightens)")
    if new or stale:
        print(f"exception-interface: {len(new)} new, {len(stale)} stale")
        return 1
    print(f"exception-interface: clean ({len(base)} classes baselined for migration)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
