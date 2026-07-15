"""Offline tests for the docstring hard gate's per-function checker.

The gate (``tools/docstring_gate.py``) is diff-aware in CI; these tests pin the
pure ``check_function`` logic — docstring presence, a ``:param:`` per named arg,
and a ``:return:`` on a real value-return — using synthetic functions, no git.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE = REPO_ROOT / "tools" / "docstring_gate.py"


def _load_gate():
    """Load tools/docstring_gate.py as a module (it is not an importable package)."""
    spec = importlib.util.spec_from_file_location("docstring_gate", GATE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gate():
    """The loaded gate module."""
    return _load_gate()


def _fn(src: str):
    """Parse a single-function source string into its FunctionDef node."""
    return ast.parse(src).body[0]


def test_compliant_function_passes(gate):
    """A docstring with every :param: and a :return: is clean."""
    node = _fn('def f(a, b):\n'
               '    """Add.\n\n    :param a: first.\n    :param b: second.\n    :return: sum.\n    """\n'
               '    return a + b\n')
    assert gate.check_function(node) == []


def test_missing_docstring_flagged(gate):
    """A function with no docstring is flagged outright."""
    assert gate.check_function(_fn('def f(a):\n    return a\n')) == ["no docstring"]


def test_missing_param_flagged(gate):
    """An undocumented parameter is named; a documented one is not."""
    node = _fn('def f(a, b):\n    """Do.\n\n    :param a: first.\n    :return: x.\n    """\n    return a\n')
    probs = gate.check_function(node)
    assert any("'b'" in p for p in probs) and not any("'a'" in p for p in probs)


def test_missing_return_flagged(gate):
    """A value-returning function with no :return: is flagged."""
    node = _fn('def f(a):\n    """Do.\n\n    :param a: first.\n    """\n    return a\n')
    assert any(":return:" in p for p in gate.check_function(node))


def test_self_only_no_return_needs_only_summary(gate):
    """A method with only self and no value-return needs just a one-line docstring."""
    assert gate.check_function(_fn('def m(self):\n    """Set it."""\n    self.x = 1\n')) == []


def test_init_does_not_require_return(gate):
    """__init__ documents its params but never needs a :return:."""
    node = _fn('def __init__(self, a):\n    """Build.\n\n    :param a: the thing.\n    """\n    self.a = a\n')
    assert gate.check_function(node) == []


def test_bare_return_none_not_treated_as_value(gate):
    """`return` / `return None` are not a value-return, so no :return: is required."""
    node = _fn('def f(a):\n    """Do.\n\n    :param a: first.\n    """\n    if a:\n        return\n    return None\n')
    assert gate.check_function(node) == []
