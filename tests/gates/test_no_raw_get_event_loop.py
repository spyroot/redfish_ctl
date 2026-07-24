"""Audit: no module calls asyncio.get_event_loop() directly.

get_event_loop() raises RuntimeError on Python 3.14 (in the CI matrix) when no
loop is running, killing every async Redfish path before it sends anything. The
package routes loop resolution through RedfishManager._event_loop(), which is
3.14-safe. This test AST-scans the package and fails if a raw call reappears —
the one sanctioned exception is the helper's own policy-form call.

Author Mus spyroot@gmail.com
"""
import ast
import pathlib

import pytest

_PKG = pathlib.Path(__file__).resolve().parents[2] / "redfish_ctl"
# The single sanctioned raw call lives inside _event_loop() itself, and it is the
# policy form asyncio.get_event_loop_policy().get_event_loop(), which the audit
# below ignores because its receiver is the policy object, not the asyncio module.


def _raw_module_get_event_loop_calls(path: pathlib.Path) -> list[int]:
    """Return line numbers of ``asyncio.get_event_loop()`` calls in one module.

    Only flags calls whose receiver is the ``asyncio`` module directly; the
    helper's ``get_event_loop_policy().get_event_loop()`` has a Call receiver and
    is not flagged.

    :param path: python source file to scan.
    :return: 1-indexed line numbers of raw module-level get_event_loop() calls.
    """
    hits: list[int] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get_event_loop"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "asyncio"):
            hits.append(node.lineno)
    return hits


def test_no_module_uses_raw_get_event_loop():
    """No redfish_ctl module may call asyncio.get_event_loop() directly."""
    offenders = {
        str(p.relative_to(_PKG.parent)): lines
        for p in sorted(_PKG.rglob("*.py"))
        if (lines := _raw_module_get_event_loop_calls(p))
    }
    assert not offenders, (
        "raw asyncio.get_event_loop() found (raises on 3.14); use "
        f"self._event_loop() instead: {offenders}")


@pytest.mark.parametrize("layer", [
    "redfish_ctl/redfish_manager.py",
    "redfish_ctl/idrac_manager.py",
])
def test_helper_available_across_layers(layer):
    """_event_loop must be resolvable from the generic layer and the base — it
    lives on RedfishManager (parent), so both inherit it."""
    src = (_PKG.parent / layer).read_text(encoding="utf-8")
    assert "self._event_loop()" in src or "def _event_loop(" in src
