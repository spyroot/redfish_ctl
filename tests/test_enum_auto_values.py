"""Guard the Python 3.13+ enum ``auto()`` contract across the package.

Since Python 3.13 the default ``enum._generate_next_value_`` sorts the values
of the already-defined members, so an ``auto()`` member that follows a member
with a non-integer value (an accidental ``()``, a string, a tuple) raises
``TypeError: unable to sort non-numeric values`` at class construction — and
the whole package fails to import. That is exactly what happened on Python
3.14 with ``ApiRequestType`` (``ConvertNoneRaid = ()`` and
``DellOemNetIsoBoot = ()``, both typos for ``auto()``). Older interpreters
(3.10–3.12) tolerate the mix silently, so the CI matrix never saw it; these
tests fail on ANY interpreter if the pattern reappears.

Author Mus spyroot@gmail.com
"""
import ast
from pathlib import Path

import redfish_ctl
from redfish_ctl.idrac_shared import ApiRequestType

PACKAGE_DIR = Path(redfish_ctl.__file__).resolve().parent

# Enum-family bases that inherit the DEFAULT int-increment
# _generate_next_value_. StrEnum is deliberately absent: it ships its own
# generator (lowercased member name, prior values ignored), so mixing string
# members with auto() is legal there and must not be flagged.
_ENUM_BASES = {"Enum", "IntEnum", "Flag", "IntFlag"}


def _base_names(class_def: ast.ClassDef) -> set:
    """Collect the plain names of a class definition's bases.

    Both ``class X(Enum)`` and ``class X(enum.Enum)`` forms resolve to the
    final attribute name, so the audit sees them the same way.

    :param class_def: the ``ast.ClassDef`` node to inspect.
    :return: set of base-class name strings.
    """
    names = set()
    for base in class_def.bases:
        if isinstance(base, ast.Name):
            names.add(base.id)
        elif isinstance(base, ast.Attribute):
            names.add(base.attr)
    return names


def _member_values(stmt: ast.stmt):
    """Yield ``(name, value node)`` for each member a statement defines.

    Handles plain assignments, annotated assignments with a value
    (``B: int = ()`` still creates an enum member, so an annotation cannot
    hide a value from the audit), and same-length tuple unpacking
    (``A, B = "x", "y"`` creates one member per name). Statements that
    create no member — annotation-only lines, method definitions — yield
    nothing.

    :param stmt: a statement from an enum class body.
    :return: iterator of ``(member name, assigned-value AST node)`` pairs.
    """
    if isinstance(stmt, ast.Assign):
        for target in stmt.targets:
            if isinstance(target, ast.Name):
                yield target.id, stmt.value
            elif (
                isinstance(target, ast.Tuple)
                and isinstance(stmt.value, ast.Tuple)
                and len(target.elts) == len(stmt.value.elts)
            ):
                for elt, value in zip(target.elts, stmt.value.elts):
                    if isinstance(elt, ast.Name):
                        yield elt.id, value
    elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
        if isinstance(stmt.target, ast.Name):
            yield stmt.target.id, stmt.value


def _is_int_literal(node: ast.AST) -> bool:
    """Report whether a value node is an integer literal.

    Unwraps a leading unary sign so an explicit negative member such as
    ``X = -1`` (an ``ast.UnaryOp``, not an ``ast.Constant``) is still
    recognized as an int — ints sort fine, so a later ``auto()`` is legal.

    :param node: the assigned-value AST node.
    :return: True when the node is a plain or sign-prefixed int literal.
    """
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        node = node.operand
    return isinstance(node, ast.Constant) and isinstance(node.value, int)


def _is_auto_call(node: ast.AST) -> bool:
    """Report whether an assignment value is a bare ``auto()`` call.

    :param node: the assigned-value AST node.
    :return: True when the node is ``auto()`` or ``enum.auto()``.
    """
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "auto"
    return isinstance(func, ast.Attribute) and func.attr == "auto"


def _enum_auto_violations(tree: ast.AST, rel_path: str) -> list:
    """Find ``auto()`` members preceded by non-integer member values.

    Walks every Enum-family class in a parsed module and flags each
    ``auto()`` member that appears after a member whose value is not an
    integer literal and not itself ``auto()`` — the exact layout Python
    3.13+ rejects at class construction. A class that defines its own
    ``_generate_next_value_`` opts out of the default sorting and is skipped.

    :param tree: parsed module AST.
    :param rel_path: package-relative path used in violation messages.
    :return: list of human-readable violation strings.
    """
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not (_base_names(node) & _ENUM_BASES):
            continue
        defines_generator = any(
            isinstance(stmt, ast.FunctionDef)
            and stmt.name == "_generate_next_value_"
            for stmt in node.body
        )
        if defines_generator:
            continue
        saw_non_int_member = False
        for stmt in node.body:
            for name, value in _member_values(stmt):
                if name.startswith("_"):
                    continue
                if _is_auto_call(value):
                    if saw_non_int_member:
                        violations.append(
                            f"{rel_path}:{stmt.lineno} {node.name}.{name} uses"
                            " auto() after a non-integer member value; Python"
                            " 3.13+ raises 'unable to sort non-numeric values'"
                            " at import. Give members explicit values or"
                            " define _generate_next_value_."
                        )
                elif not _is_int_literal(value):
                    saw_non_int_member = True
    return violations


def test_no_enum_mixes_auto_with_non_integer_values():
    """No Enum in the package may define auto() after a non-integer member.

    This statically enforces the Python 3.13+ ``_generate_next_value_``
    contract on every interpreter in the CI matrix, so the 3.14 import
    breakage cannot silently return while CI runs older pythons.
    """
    violations = []
    for py_file in sorted(PACKAGE_DIR.rglob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        rel = py_file.relative_to(PACKAGE_DIR.parent).as_posix()
        violations.extend(_enum_auto_violations(tree, rel))
    assert not violations, "\n".join(violations)


def test_api_request_type_values_are_all_int():
    """Every ApiRequestType member carries an auto()-generated int value.

    Regression for ``ConvertNoneRaid = ()`` / ``DellOemNetIsoBoot = ()``:
    an accidental non-integer value poisons every later ``auto()`` on
    Python 3.13+ and breaks package import.
    """
    non_int = [m.name for m in ApiRequestType if not isinstance(m.value, int)]
    assert non_int == []


def test_audit_flags_the_original_breakage():
    """The AST audit detects the exact pre-fix ApiRequestType layout.

    Feeds the auditor a minimal enum with the historical ``()`` typo followed
    by ``auto()`` and expects a violation, proving the guard actually fires.
    """
    source = (
        "from enum import Enum, auto\n"
        "class Broken(Enum):\n"
        "    A = auto()\n"
        "    B = ()\n"
        "    C = auto()\n"
    )
    violations = _enum_auto_violations(ast.parse(source), "broken.py")
    assert len(violations) == 1
    assert "Broken.C" in violations[0]


def test_audit_flags_annotated_member():
    """An annotated assignment cannot hide a non-integer member value.

    ``B: tuple = ()`` still creates an enum member, so the audit tracks it
    exactly like a plain assignment; skipping AnnAssign nodes would let the
    original breakage return behind a type annotation.
    """
    source = (
        "from enum import Enum, auto\n"
        "class Broken(Enum):\n"
        "    B: tuple = ()\n"
        "    C = auto()\n"
    )
    violations = _enum_auto_violations(ast.parse(source), "annotated.py")
    assert len(violations) == 1
    assert "Broken.C" in violations[0]


def test_audit_accepts_negative_int_members():
    """An explicit negative int member does not poison later auto() calls.

    ``X = -1`` parses as ``ast.UnaryOp``, not ``ast.Constant``; ints sort
    fine on every interpreter, so the audit must not flag the layout
    (reviewer-found false positive).
    """
    source = (
        "from enum import Enum, auto\n"
        "class Levels(Enum):\n"
        "    Below = -1\n"
        "    Above = auto()\n"
    )
    assert _enum_auto_violations(ast.parse(source), "levels.py") == []


def test_audit_flags_tuple_unpacked_members():
    """Tuple-unpacked members with non-int values are still tracked.

    ``A, B = "x", "y"`` creates one member per name, so a later ``auto()``
    breaks import on Python 3.13+ exactly as with plain assignments
    (reviewer-found false negative).
    """
    source = (
        "from enum import Enum, auto\n"
        "class Broken(Enum):\n"
        "    A, B = 'x', 'y'\n"
        "    C = auto()\n"
    )
    violations = _enum_auto_violations(ast.parse(source), "unpacked.py")
    assert len(violations) == 1
    assert "Broken.C" in violations[0]


def test_audit_exempts_strenum():
    """StrEnum legally mixes string members with auto().

    ``StrEnum._generate_next_value_`` returns the lowercased member name and
    ignores prior values, so no sorting happens and the audit must not flag
    such classes.
    """
    source = (
        "from enum import StrEnum, auto\n"
        "class Names(StrEnum):\n"
        "    A = 'explicit'\n"
        "    B = auto()\n"
    )
    assert _enum_auto_violations(ast.parse(source), "strenum.py") == []


def test_audit_accepts_custom_generator():
    """An enum with its own _generate_next_value_ is exempt from the audit.

    The 3.13+ sorting lives in the DEFAULT generator; a class that overrides
    it defines its own contract and must not be flagged.
    """
    source = (
        "from enum import Enum, auto\n"
        "class Custom(Enum):\n"
        "    def _generate_next_value_(name, start, count, last_values):\n"
        "        return name\n"
        "    A = 'x'\n"
        "    B = auto()\n"
    )
    assert _enum_auto_violations(ast.parse(source), "custom.py") == []
