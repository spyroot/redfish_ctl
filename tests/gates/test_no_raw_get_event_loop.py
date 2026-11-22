"""Audit: event-loop lookup stays centralized and avoids policy APIs.

get_event_loop() raises RuntimeError on Python 3.14 (in the CI matrix) when no
loop is running, killing every async Redfish path before it sends anything. The
package routes loop resolution through RedfishManager._event_loop(). This test
AST-scans the package, permits the helper's single direct lookup, and rejects
policy lookup everywhere.

Author Mus spyroot@gmail.com
"""
import ast
import pathlib

import pytest

_PKG = pathlib.Path(__file__).resolve().parents[2] / "redfish_ctl"
_LOOP_HELPER = _PKG / "redfish_manager.py"


def _asyncio_attr_call_lines(path: pathlib.Path, attr: str) -> list[int]:
    """Return line numbers of ``asyncio.<attr>()`` calls in one module.

    :param path: Python source file to scan.
    :param attr: asyncio attribute name to reject.
    :return: 1-indexed line numbers, including aliased and direct imports.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    module_names = {"asyncio"}
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            module_names.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "asyncio"
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "asyncio":
            imported_names.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == attr
            )

    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        is_module_call = (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == attr
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in module_names
        )
        is_imported_call = (
            isinstance(node.func, ast.Name)
            and node.func.id in imported_names
        )
        if is_module_call or is_imported_call:
            hits.append(node.lineno)
    return sorted(hits)


def _allowed_helper_call_lines(path: pathlib.Path, attr: str) -> set[int]:
    """Return calls allowed inside the exact shared loop helper."""
    if path.resolve() != _LOOP_HELPER.resolve():
        return set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for class_node in tree.body:
        if not (
            isinstance(class_node, ast.ClassDef)
            and class_node.name == "RedfishManager"
        ):
            continue
        for node in class_node.body:
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "_event_loop"
            ):
                return {
                    line for line in _asyncio_attr_call_lines(path, attr)
                    if node.lineno <= line <= node.end_lineno
                }
    return set()


def _raw_module_get_event_loop_calls(path: pathlib.Path) -> list[int]:
    """Return disallowed ``asyncio.get_event_loop()`` call line numbers.

    The single direct call inside ``RedfishManager._event_loop`` is allowed so
    the helper can reuse a loop already installed for a synchronous caller.
    """
    allowed = _allowed_helper_call_lines(path, "get_event_loop")
    return [
        line for line in _asyncio_attr_call_lines(path, "get_event_loop")
        if line not in allowed
    ]


def _deprecated_policy_calls(path: pathlib.Path) -> list[int]:
    """Return disallowed ``asyncio.get_event_loop_policy()`` call lines."""
    return _asyncio_attr_call_lines(path, "get_event_loop_policy")


def test_audit_detects_uncentralized_get_event_loop_and_deprecated_policy(tmp_path):
    module = tmp_path / "uses_asyncio.py"
    module.write_text(
        "\n".join([
            "import asyncio",
            "import asyncio as aio",
            "from asyncio import get_event_loop as imported_loop",
            "from asyncio import get_event_loop_policy as policy_lookup",
            "loop = asyncio.get_event_loop()",
            "alias_loop = aio.get_event_loop()",
            "direct_loop = imported_loop()",
            "policy_loop = policy_lookup().get_event_loop()",
        ]),
        encoding="utf-8",
    )

    assert _raw_module_get_event_loop_calls(module) == [5, 6, 7]
    assert _deprecated_policy_calls(module) == [8]


def test_no_module_uses_raw_get_event_loop():
    offenders = {
        str(p.relative_to(_PKG.parent)): lines
        for p in sorted(_PKG.rglob("*.py"))
        if (lines := _raw_module_get_event_loop_calls(p))
    }
    assert not offenders, (
        "asyncio.get_event_loop() found outside the shared helper; use "
        f"self._event_loop() instead: {offenders}")


def test_shared_helper_contains_one_direct_event_loop_lookup():
    assert len(_asyncio_attr_call_lines(_LOOP_HELPER, "get_event_loop")) == 1
    assert len(_asyncio_attr_call_lines(_LOOP_HELPER, "get_event_loop_policy")) == 0
    assert _raw_module_get_event_loop_calls(_LOOP_HELPER) == []
    assert _deprecated_policy_calls(_LOOP_HELPER) == []


def test_no_module_uses_deprecated_event_loop_policy():
    offenders = {
        str(p.relative_to(_PKG.parent)): lines
        for p in sorted(_PKG.rglob("*.py"))
        if (lines := _deprecated_policy_calls(p))
    }
    assert not offenders, (
        "deprecated asyncio.get_event_loop_policy() found; use the supported "
        f"RedfishManager._event_loop() helper instead: {offenders}")


@pytest.mark.parametrize("layer", [
    "redfish_ctl/redfish_manager.py",
    "redfish_ctl/idrac_manager.py",
])
def test_helper_available_across_layers(layer):
    """_event_loop must be resolvable from the generic layer and the base — it
    lives on RedfishManager (parent), so both inherit it."""
    src = (_PKG.parent / layer).read_text(encoding="utf-8")
    assert "self._event_loop()" in src or "def _event_loop(" in src
