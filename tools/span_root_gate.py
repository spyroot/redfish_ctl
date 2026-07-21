"""Gate: every BMC HTTP call sits under a tracing span (single-root traces).

One command opens one root span (``tracing.operation_span``); each outbound BMC
call opens a CLIENT child (``tracing.client_span``). A ``requests.<verb>`` that
runs outside any span emits no span at all - the trace loses a hop and the call
is orphaned from the operation root. This gate flags such a call, whether it is
invoked directly or passed to an executor via ``functools.partial(requests.get,
...)``.

    python3 tools/span_root_gate.py

Ratchet: known orphaned calls are grandfathered in tools/span_root_baseline.txt;
a NEW un-spanned call fails, and a wrapped one must leave the baseline. The
loader/tracing modules that define the primitives are exempt.

Author Mus spyroot@gmail.com
"""
from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

_VERBS = {"get", "post", "put", "patch", "delete", "head"}
_SPAN_FNS = {"client_span", "operation_span"}
# A function that hands its request to one of these wrappers is traced: the sync
# path spans the partial in traced_request; the async/executor path spans it in
# traced_request_callable; the inline path uses a `with client_span` block.
_WRAPPERS = {"traced_request", "traced_request_callable"}
_BASELINE = pathlib.Path(__file__).parent / "span_root_baseline.txt"


def _fn_name(call: ast.Call) -> str | None:
    """Return the called function's bare name.

    :param call: a Call node.
    :return: the attribute or plain name, or None.
    """
    fn = call.func
    return fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)


def _calls_wrapper(fn_node: ast.AST) -> bool:
    """Return whether a function body hands a request to the tracing wrapper.

    :param fn_node: a FunctionDef/AsyncFunctionDef node.
    :return: True when its body calls a tracing wrapper.
    """
    return any(isinstance(n, ast.Call) and _fn_name(n) in _WRAPPERS
               for n in ast.walk(fn_node))


def _is_span_with(node: ast.withitem) -> bool:
    """Return whether a ``with`` item opens a tracing span.

    :param node: a withitem from a ``with`` statement.
    :return: True when its context expression calls client_span/operation_span.
    """
    call = node.context_expr
    if not isinstance(call, ast.Call):
        return False
    fn = call.func
    name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
    return name in _SPAN_FNS


def _is_requests_verb(node: ast.AST) -> bool:
    """Return whether a node references ``requests.<verb>``.

    Matches both the direct call ``requests.get(...)`` and the bare attribute
    ``requests.get`` handed to ``functools.partial``/an executor.

    :param node: any AST node.
    :return: True for a requests HTTP-verb attribute reference.
    """
    if isinstance(node, ast.Attribute) and node.attr in _VERBS:
        base = node.value
        return isinstance(base, ast.Name) and base.id == "requests"
    return False


def _walk(node: ast.AST, traced: bool, path: str, out: list[str]) -> None:
    """Recurse, tracking whether the cursor is under tracing.

    A ``requests.<verb>`` is traced when it sits inside a ``with client_span`` /
    ``operation_span`` block (inline path) or inside a function that hands its
    request to a wrapper in :data:`_WRAPPERS` (deferred path). Else it is an orphan.

    :param node: current AST node.
    :param traced: True when an enclosing span/wrapper covers this subtree.
    :param path: source path, for reporting.
    :param out: accumulator of ``"path:line"`` violations.
    :return: None; ``out`` is mutated in place.
    """
    if _is_requests_verb(node) and not traced:
        out.append(f"{path}:{node.lineno}")
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        fn_traced = traced or _calls_wrapper(node)
        for child in ast.iter_child_nodes(node):
            _walk(child, fn_traced, path, out)
        return
    if isinstance(node, ast.With):
        body_traced = traced or any(_is_span_with(it) for it in node.items)
        for it in node.items:
            _walk(it.context_expr, traced, path, out)
        for child in node.body:
            _walk(child, body_traced, path, out)
        return
    for child in ast.iter_child_nodes(node):
        _walk(child, traced, path, out)


def _violations() -> list[str]:
    """Return ``path:line`` for every un-spanned ``requests.<verb>`` reference.

    :return: sorted ``"path:line"`` strings.
    """
    out: list[str] = []
    files = subprocess.check_output(
        ["git", "ls-files", "redfish_ctl/*.py", "redfish_ctl/**/*.py"]).decode().split()
    for f in files:
        if f.endswith(("config.py", "telemetry/tracing.py")):
            continue
        tree = ast.parse(pathlib.Path(f).read_text(encoding="utf-8"))
        _walk(tree, False, f, out)  # module scope is untraced by default
    return sorted(set(out))


def _baseline() -> set[str]:
    """Return grandfathered orphaned-call locations.

    :return: the allowed pre-existing ``"path:line"`` entries.
    """
    if not _BASELINE.exists():
        return set()
    return {ln.strip() for ln in _BASELINE.read_text().splitlines()
            if ln.strip() and not ln.startswith("#")}


def main() -> int:
    """Report new un-spanned BMC calls and stale baseline entries.

    :return: 0 when clean, 1 on a new orphan or a stale baseline entry.
    """
    base = _baseline()
    viol = set(_violations())
    new = sorted(viol - base)
    stale = sorted(base - viol)
    for v in new:
        print(f"span-root: {v} - requests.<verb> runs outside a tracing span; "
              "wrap it in tracing.client_span so the call joins the trace")
    for v in stale:
        print(f"span-root: {v} baselined but now spanned - "
              "remove it from the baseline (ratchet tightens)")
    if new or stale:
        print(f"span-root: {len(new)} new, {len(stale)} stale")
        return 1
    print(f"span-root: clean ({len(base)} orphaned call(s) baselined)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
