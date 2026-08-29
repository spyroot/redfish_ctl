"""Gate: every BMC HTTP call sits under a tracing span (single-root traces).

One command opens one root span (``tracing.operation_span``); each outbound BMC
call opens a CLIENT child (``tracing.client_span``). A direct transport call
that runs outside any span emits no span at all - the trace loses a hop and the
call is orphaned from the operation root. This gate resolves imported aliases,
directly imported functions, and bound client/session instances before checking
the call site.

    python3 tools/span_root_gate.py

Ratchet: known orphaned calls are grandfathered in tools/span_root_baseline.txt;
a NEW un-spanned call fails, and a wrapped one must leave the baseline. The
configuration loader, tracing primitives, and non-BMC telemetry transports are
exempt.

Author Mus spyroot@gmail.com
"""
from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

_VERBS = {
    "get",
    "post",
    "put",
    "patch",
    "delete",
    "head",
    "options",
    "request",
}
_SPAN_FNS = {"client_span", "operation_span"}
# A function that hands its request to one of these wrappers is traced: the sync
# path spans the partial in traced_request; the async/executor path spans it in
# traced_request_callable; the inline path uses a `with client_span` block.
_WRAPPERS = {"traced_request", "traced_request_callable"}
_BASELINE = pathlib.Path(__file__).parent / "span_root_baseline.txt"

_DIRECT_HTTP_FUNCTIONS = {
    *(f"requests.{verb}" for verb in _VERBS),
    *(f"httpx.{verb}" for verb in _VERBS),
    "aiohttp.request",
    "urllib.request.urlopen",
    "urllib3.request",
}
_CLIENT_METHODS = {
    "requests.Session": _VERBS,
    "httpx.Client": _VERBS,
    "httpx.AsyncClient": _VERBS,
    "aiohttp.ClientSession": _VERBS,
    "http.client.HTTPConnection": {"request"},
    "http.client.HTTPSConnection": {"request"},
    "urllib.request.OpenerDirector": {"open"},
    "urllib3.PoolManager": {"request"},
    "urllib3.ProxyManager": {"request"},
}
_FACTORY_RESULTS = {
    "urllib.request.build_opener": "urllib.request.OpenerDirector",
}
_HTTP_MODULES = {"requests", "httpx", "aiohttp", "urllib.request", "urllib3"}
_EXEMPT_FILES = {
    "redfish_ctl/config.py",
    "redfish_ctl/telemetry/exporter.py",
    "redfish_ctl/telemetry/http_util.py",
    "redfish_ctl/telemetry/tracing.py",
}


def _target_name(node: ast.AST) -> str | None:
    """Return a stable name for an assignment target.

    :param node: a Name or dotted Attribute target.
    :return: the dotted target name, or None for destructuring/subscripts.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _target_name(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _scope_nodes(root: ast.AST) -> list[ast.AST]:
    """Return nodes owned by one lexical scope in source order.

    Nested functions, classes, and lambdas are returned as declarations but
    their bodies are left for a child resolver. This prevents a local alias in
    one function from overwriting the same local name in another function.

    :param root: module, class, function, or lambda that owns the scope.
    :return: scope-owned nodes ordered by source location.
    """
    nodes: list[ast.AST] = []
    stack = list(reversed(list(ast.iter_child_nodes(root))))
    while stack:
        node = stack.pop()
        nodes.append(node)
        if isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda),
        ):
            continue
        stack.extend(reversed(list(ast.iter_child_nodes(node))))
    return sorted(
        nodes,
        key=lambda item: (
            getattr(item, "lineno", -1),
            getattr(item, "col_offset", -1),
        ),
    )


class _Bindings:
    """Resolve import aliases and statically visible HTTP client bindings."""

    def __init__(self, tree: ast.AST, parent: _Bindings | None = None):
        """Build bindings for one parsed module.

        :param tree: parsed module or test snippet.
        :param parent: enclosing lexical-scope bindings, if any.
        """
        self.parent = parent
        self.nodes = _scope_nodes(tree)
        self.aliases: dict[str, set[str]] = {}
        self.values: dict[str, set[str]] = {}
        self.returns: dict[str, set[str]] = {}
        self.local_names: set[str] = set()
        self._read_imports()
        self._read_return_annotations(tree)
        self._read_value_bindings()

    @staticmethod
    def _bind(mapping: dict[str, set[str]], name: str, values: set[str]) -> None:
        """Union resolved identities into one local binding.

        :param mapping: alias, value, or return binding map.
        :param name: local identifier being bound.
        :param values: resolved identities to add.
        """
        if values:
            mapping.setdefault(name, set()).update(values)

    def _read_imports(self) -> None:
        """Record import aliases without importing the target packages."""
        for node in self.nodes:
            if isinstance(node, ast.Import):
                for item in node.names:
                    local_name = item.asname or item.name.split(".", 1)[0]
                    self.local_names.add(local_name)
                    if item.asname:
                        self._bind(self.aliases, item.asname, {item.name})
                    else:
                        root = item.name.split(".", 1)[0]
                        self._bind(self.aliases, root, {root})
            elif isinstance(node, ast.ImportFrom) and node.module:
                for item in node.names:
                    if item.name == "*":
                        continue
                    local_name = item.asname or item.name
                    self.local_names.add(local_name)
                    self._bind(
                        self.aliases,
                        local_name,
                        {f"{node.module}.{item.name}"},
                    )

    def _read_return_annotations(self, tree: ast.AST) -> None:
        """Record local function return types used by client factories."""
        for node in self.nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.local_names.add(node.name)
                resolved = self.resolve(node.returns) if node.returns else set()
                self._bind(
                    self.returns,
                    node.name,
                    resolved.intersection(_CLIENT_METHODS),
                )
        if isinstance(tree, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for arg in (
                *tree.args.posonlyargs,
                *tree.args.args,
                *tree.args.kwonlyargs,
            ):
                self.local_names.add(arg.arg)
                annotation = self.resolve(arg.annotation) if arg.annotation else set()
                self._bind(
                    self.values,
                    arg.arg,
                    annotation.intersection(_CLIENT_METHODS),
                )

    def _read_value_bindings(self) -> None:
        """Propagate constructor, factory, annotation, and simple alias values."""
        for node in self.nodes:
            pairs: list[tuple[ast.AST, ast.AST | None]] = []
            if isinstance(node, ast.Assign):
                pairs.extend((target, node.value) for target in node.targets)
            elif isinstance(node, ast.AnnAssign):
                pairs.append((node.target, node.value or node.annotation))
            elif isinstance(node, ast.NamedExpr):
                pairs.append((node.target, node.value))
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                pairs.extend(
                    (item.optional_vars, item.context_expr)
                    for item in node.items
                    if item.optional_vars is not None
                )
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                pairs.append((node.target, None))
            for target, value in pairs:
                name = _target_name(target)
                if not name:
                    continue
                self.local_names.add(name)
                self._bind(self.values, name, self._value_result(value))

    def _value_result(self, node: ast.AST | None) -> set[str]:
        """Return the transport identity produced by an expression."""
        if node is None:
            return set()
        resolved = self.resolve(node)
        return {
            item
            for item in resolved
            if item in _CLIENT_METHODS
            or item in _DIRECT_HTTP_FUNCTIONS
            or item in _HTTP_MODULES
        }

    def _resolve_name(self, name: str) -> set[str]:
        """Resolve one name without crossing a local shadowing boundary."""
        resolved = self.values.get(name, set()) | self.aliases.get(name, set())
        if resolved:
            return resolved
        if name in self.local_names:
            return {name}
        if self.parent is not None:
            return self.parent._resolve_name(name)
        return {name}

    def _resolve_return(self, name: str) -> set[str]:
        """Resolve a local or enclosing function's annotated client result."""
        if name in self.returns:
            return self.returns[name]
        if name in self.local_names:
            return set()
        if self.parent is not None:
            return self.parent._resolve_return(name)
        return set()

    def resolve(self, node: ast.AST | None) -> set[str]:
        """Resolve a Name/Attribute/Call to its imported transport identity."""
        if isinstance(node, ast.Name):
            return self._resolve_name(node.id)
        if isinstance(node, ast.Attribute):
            resolved: set[str] = set()
            for base in self.resolve(node.value):
                dotted = f"{base}.{node.attr}"
                resolved.update(self.values.get(dotted, {dotted}))
            return resolved
        if isinstance(node, ast.Call):
            resolved: set[str] = set()
            for called in self.resolve(node.func):
                if called in _CLIENT_METHODS:
                    resolved.add(called)
                elif called in _FACTORY_RESULTS:
                    resolved.add(_FACTORY_RESULTS[called])
                else:
                    resolved.update(self._resolve_return(called.rsplit(".", 1)[-1]))
            return resolved
        return set()


def _fn_name(call: ast.Call) -> str | None:
    """Return the called function's bare name.

    :param call: a Call node.
    :return: the attribute or plain name, or None.
    """
    fn = call.func
    return fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)


def _wrapper_bound_names(scope: ast.AST) -> set[str]:
    """Return local values handed directly to a tracing wrapper.

    :param scope: function, class, lambda, or module scope.
    :return: names whose assigned callables are consumed by a wrapper.
    """
    names: set[str] = set()
    for node in _scope_nodes(scope):
        if not isinstance(node, ast.Call) or _fn_name(node) not in _WRAPPERS:
            continue
        for argument in (*node.args, *(item.value for item in node.keywords)):
            if isinstance(argument, ast.Name):
                names.add(argument.id)
    return names


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


def _http_symbol(node: ast.AST, bindings: _Bindings) -> str | None:
    """Return the transport symbol referenced by an AST node.

    Both direct calls and bare function references are covered, so handing an
    aliased verb to ``functools.partial`` cannot evade the gate.

    :param node: any AST node.
    :param bindings: import and client bindings for the containing module.
    :return: the resolved HTTP symbol, or None for non-transport expressions.
    """
    if not isinstance(node, (ast.Name, ast.Attribute)):
        return None
    if not isinstance(getattr(node, "ctx", ast.Load()), ast.Load):
        return None
    resolved = bindings.resolve(node)
    direct = resolved.intersection(_DIRECT_HTTP_FUNCTIONS)
    if direct:
        return sorted(direct)[0]
    if isinstance(node, ast.Attribute):
        for client in bindings.resolve(node.value):
            methods = _CLIENT_METHODS.get(client, set())
            if node.attr in methods:
                return f"{client}.{node.attr}"
    return None


def _assignment_parts(
    node: ast.AST,
) -> tuple[tuple[ast.AST, ...], ast.AST | None] | None:
    """Return assignment targets and value for wrapper-flow handling."""
    if isinstance(node, ast.Assign):
        return tuple(node.targets), node.value
    if isinstance(node, ast.AnnAssign):
        return (node.target,), node.value
    if isinstance(node, ast.NamedExpr):
        return (node.target,), node.value
    return None


def _walk(
        node: ast.AST,
        traced: bool,
        path: str,
        out: list[str],
        bindings: _Bindings | None = None,
        wrapped_names: set[str] | None = None,
) -> None:
    """Recurse, tracking whether the cursor is under tracing.

    A direct HTTP call is traced when it sits inside a ``with client_span`` /
    ``operation_span`` block (inline path) or inside a function that hands its
    request to a wrapper in :data:`_WRAPPERS` (deferred path). Otherwise it is
    an orphan.

    :param node: current AST node.
    :param traced: True when an enclosing span/wrapper covers this subtree.
    :param path: source path, for reporting.
    :param out: accumulator of ``"path:line"`` violations.
    :param bindings: lexical import and client binding resolver.
    :param wrapped_names: local callables handed to a tracing wrapper.
    :return: None; ``out`` is mutated in place.
    """
    if bindings is None:
        bindings = _Bindings(node)
    if wrapped_names is None:
        wrapped_names = _wrapper_bound_names(node)
    if _http_symbol(node, bindings) and not traced:
        out.append(f"{path}:{node.lineno}")
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        function_bindings = _Bindings(node, bindings)
        function_wrapped_names = _wrapper_bound_names(node)
        for child in ast.iter_child_nodes(node):
            _walk(
                child,
                traced,
                path,
                out,
                function_bindings,
                function_wrapped_names,
            )
        return
    if isinstance(node, (ast.ClassDef, ast.Lambda)):
        child_bindings = _Bindings(node, bindings)
        child_wrapped_names = _wrapper_bound_names(node)
        for child in ast.iter_child_nodes(node):
            _walk(
                child,
                traced,
                path,
                out,
                child_bindings,
                child_wrapped_names,
            )
        return
    if isinstance(node, ast.Call) and _fn_name(node) in _WRAPPERS:
        _walk(node.func, traced, path, out, bindings, wrapped_names)
        for argument in node.args:
            _walk(argument, True, path, out, bindings, wrapped_names)
        for keyword in node.keywords:
            _walk(keyword.value, True, path, out, bindings, wrapped_names)
        return
    assignment = _assignment_parts(node)
    if assignment is not None:
        targets, value = assignment
        assignment_is_wrapped = any(
            _target_name(target) in wrapped_names for target in targets
        )
        for target in targets:
            _walk(target, traced, path, out, bindings, wrapped_names)
        if value is not None:
            _walk(
                value,
                traced or assignment_is_wrapped,
                path,
                out,
                bindings,
                wrapped_names,
            )
        return
    if isinstance(node, (ast.With, ast.AsyncWith)):
        body_traced = traced or any(_is_span_with(it) for it in node.items)
        for it in node.items:
            _walk(it.context_expr, traced, path, out, bindings, wrapped_names)
        for child in node.body:
            _walk(child, body_traced, path, out, bindings, wrapped_names)
        return
    for child in ast.iter_child_nodes(node):
        _walk(child, traced, path, out, bindings, wrapped_names)


def _violations() -> list[str]:
    """Return ``path:line`` for every un-spanned direct HTTP reference.

    :return: sorted ``"path:line"`` strings.
    """
    out: list[str] = []
    files = subprocess.check_output(
        ["git", "ls-files", "redfish_ctl/*.py", "redfish_ctl/**/*.py"]).decode().split()
    for f in files:
        if f in _EXEMPT_FILES:
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
        print(f"span-root: {v} - a direct HTTP call runs outside a tracing span; "
              "route it through a traced transport or wrap it in "
              "tracing.client_span so the call joins the trace")
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
