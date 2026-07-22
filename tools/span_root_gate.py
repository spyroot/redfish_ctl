"""Gate: every BMC HTTP call sits under an exact CLIENT-span boundary.

One command opens one root span (``tracing.operation_span``); each outbound BMC
call opens a CLIENT child (``tracing.client_span``). The static half detects
module aliases, imported verbs, and Session/client methods. Coverage is scoped
to the exact ``client_span`` body or callable handed to a tracing wrapper; a
wrapper elsewhere in the function does not suppress an unrelated raw call.

    python3 tools/span_root_gate.py

Ratchet: known orphaned calls are grandfathered in tools/span_root_baseline.txt;
a NEW un-spanned call fails, and a wrapped one must leave the baseline. Runtime
parent, kind, link, attribute, and leakage assertions run from the gate wrapper.
The loader/tracing modules that define the primitives are exempt.

Author Mus spyroot@gmail.com
"""
from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

_VERBS = {"get", "post", "put", "patch", "delete", "head", "request"}
_HTTP_MODULES = {"requests", "httpx", "aiohttp", "urllib3"}
_CLIENT_FACTORIES = {"Session", "Client", "AsyncClient", "PoolManager"}
_WRAPPERS = {"traced_request", "traced_request_callable"}
_BASELINE = pathlib.Path(__file__).parent / "span_root_baseline.txt"


def _fn_name(call: ast.Call) -> str | None:
    """Return the called function's bare name.

    :param call: a Call node.
    :return: the attribute or plain name, or None.
    """
    fn = call.func
    return fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)


def _is_client_span_with(node: ast.withitem) -> bool:
    """Return whether a ``with`` item opens a CLIENT span.

    :param node: a withitem from a ``with`` statement.
    :return: True when its context expression calls ``client_span``.
    """
    call = node.context_expr
    if not isinstance(call, ast.Call):
        return False
    return _fn_name(call) == "client_span"


def _target_names(node: ast.AST) -> set[str]:
    """Return simple names assigned by an assignment target.

    :param node: assignment target node.
    :return: assigned simple/attribute names.
    """
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Attribute):
        return {node.attr}
    if isinstance(node, (ast.Tuple, ast.List)):
        names: set[str] = set()
        for item in node.elts:
            names.update(_target_names(item))
        return names
    return set()


def _http_bindings(tree: ast.AST) -> tuple[set[str], set[str], set[str]]:
    """Collect HTTP module, function, and client aliases from one module.

    :param tree: parsed source tree.
    :return: module aliases, imported verb aliases, and HTTP client variables.
    """
    module_aliases: set[str] = set()
    function_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in _HTTP_MODULES:
                    module_aliases.add(alias.asname or root)
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".", 1)[0]
            if root in _HTTP_MODULES:
                for alias in node.names:
                    if alias.name in _VERBS:
                        function_aliases.add(alias.asname or alias.name)

    client_names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if not isinstance(value, ast.Call):
            continue
        factory = value.func
        is_factory = (
            isinstance(factory, ast.Attribute)
            and factory.attr in _CLIENT_FACTORIES
            and isinstance(factory.value, ast.Name)
            and factory.value.id in module_aliases
        )
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        assigned = set().union(*(_target_names(target) for target in targets))
        if is_factory or any("session" in name.lower() for name in assigned):
            client_names.update(assigned)
    return module_aliases, function_aliases, client_names


def _is_http_reference(
    node: ast.AST,
    module_aliases: set[str],
    function_aliases: set[str],
    client_names: set[str],
) -> bool:
    """Return whether a node is an outbound HTTP callable reference.

    :param node: any AST node.
    :param module_aliases: imported HTTP module names.
    :param function_aliases: directly imported HTTP verb names.
    :param client_names: variables known to hold HTTP clients/sessions.
    :return: True for a direct module verb, imported alias, or client verb.
    """
    if isinstance(node, ast.Attribute) and node.attr in _VERBS:
        base = node.value
        if isinstance(base, ast.Name):
            return base.id in module_aliases or base.id in client_names
        if isinstance(base, ast.Attribute):
            return base.attr in client_names or "session" in base.attr.lower()
    if isinstance(node, ast.Name) and node.id in function_aliases:
        return True
    return False


def _request_references(
    node: ast.AST,
    bindings: tuple[set[str], set[str], set[str]],
) -> list[ast.AST]:
    """Return HTTP callable references contained in a subtree.

    :param node: AST subtree to inspect.
    :param bindings: module, function, and client aliases.
    :return: matching callable-reference nodes.
    """
    return [
        child for child in ast.walk(node)
        if _is_http_reference(child, *bindings)
    ]


def _assignment_references(
    tree: ast.AST,
    bindings: tuple[set[str], set[str], set[str]],
) -> tuple[dict[tuple[int, str], set[int]], dict[int, int]]:
    """Map request-callable variables to the exact references they contain.

    :param tree: parsed source tree.
    :param bindings: module, function, and client aliases.
    :return: scoped assignment references plus a node-to-scope map.
    """
    scope_by_node: dict[int, int] = {}

    def map_scopes(node: ast.AST, scope: int) -> None:
        """Assign each descendant to its nearest function/lambda scope.

        :param node: current AST node.
        :param scope: enclosing scope object id.
        """
        current_scope = scope
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            current_scope = id(node)
        scope_by_node[id(node)] = current_scope
        for child in ast.iter_child_nodes(node):
            map_scopes(child, current_scope)

    map_scopes(tree, id(tree))
    assigned: dict[tuple[int, str], set[int]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
            value = node.value
        else:
            continue
        refs = {id(ref) for ref in _request_references(value, bindings)}
        if not refs:
            continue
        for target in targets:
            for name in _target_names(target):
                key = (scope_by_node[id(node)], name)
                assigned.setdefault(key, set()).update(refs)
    return assigned, scope_by_node


def _safe_reference_ids(
    tree: ast.AST,
    bindings: tuple[set[str], set[str], set[str]],
    traced: bool,
) -> set[int]:
    """Return HTTP references covered by an exact CLIENT-span boundary.

    :param tree: parsed source tree.
    :param bindings: module, function, and client aliases.
    :param traced: whether the entire supplied subtree is already a CLIENT span.
    :return: object ids for HTTP references proven to be traced.
    """
    if traced:
        return {id(ref) for ref in _request_references(tree, bindings)}

    safe: set[int] = set()
    assignments, scope_by_node = _assignment_references(tree, bindings)
    for node in ast.walk(tree):
        if isinstance(node, (ast.With, ast.AsyncWith)) and any(
            _is_client_span_with(item) for item in node.items
        ):
            for statement in node.body:
                safe.update(
                    id(ref) for ref in _request_references(statement, bindings)
                )
        if isinstance(node, ast.Call) and _fn_name(node) in _WRAPPERS:
            scope = scope_by_node[id(node)]
            request_arguments = list(node.args[2:3])
            request_arguments.extend(
                keyword.value for keyword in node.keywords
                if keyword.arg == "request_call"
            )
            for argument in request_arguments:
                safe.update(
                    id(ref) for ref in _request_references(argument, bindings)
                )
                for name in (
                    child.id for child in ast.walk(argument)
                    if isinstance(child, ast.Name)
                ):
                    safe.update(assignments.get((scope, name), set()))
    return safe


def _walk(node: ast.AST, traced: bool, path: str, out: list[str]) -> None:
    """Find exact outbound HTTP references without CLIENT-span coverage.

    :param node: current AST node.
    :param traced: True when the supplied subtree is already under client_span.
    :param path: source path, for reporting.
    :param out: accumulator of ``"path:line"`` violations.
    :return: None; ``out`` is mutated in place.
    """
    bindings = _http_bindings(node)
    safe = _safe_reference_ids(node, bindings, traced)
    violations = {
        f"{path}:{reference.lineno}"
        for reference in _request_references(node, bindings)
        if id(reference) not in safe
    }
    out.extend(sorted(violations))


def _violations() -> list[str]:
    """Return ``path:line`` for every un-spanned HTTP callable reference.

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
        print(f"span-root: {v} - HTTP callable runs outside a CLIENT span; "
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
