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
loader/tracing modules that define the primitives are exempt.

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


class _Bindings:
    """Resolve import aliases and statically visible HTTP client bindings."""

    def __init__(self, tree: ast.AST):
        """Build bindings for one parsed module.

        :param tree: parsed module or test snippet.
        """
        self.aliases: dict[str, str] = {}
        self.values: dict[str, str] = {}
        self.returns: dict[str, str] = {}
        self._read_imports(tree)
        self._read_return_annotations(tree)
        self._read_value_bindings(tree)

    def _read_imports(self, tree: ast.AST) -> None:
        """Record import aliases without importing the target packages."""
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for item in node.names:
                    if item.asname:
                        self.aliases[item.asname] = item.name
                    else:
                        root = item.name.split(".", 1)[0]
                        self.aliases[root] = root
            elif isinstance(node, ast.ImportFrom) and node.module:
                for item in node.names:
                    if item.name == "*":
                        continue
                    local_name = item.asname or item.name
                    self.aliases[local_name] = f"{node.module}.{item.name}"

    def _read_return_annotations(self, tree: ast.AST) -> None:
        """Record local function return types used by client factories."""
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            resolved = self.resolve(node.returns) if node.returns else None
            if resolved in _CLIENT_METHODS:
                self.returns[node.name] = resolved
            for arg in (*node.args.posonlyargs, *node.args.args,
                        *node.args.kwonlyargs):
                annotation = self.resolve(arg.annotation) if arg.annotation else None
                if annotation in _CLIENT_METHODS:
                    self.values[arg.arg] = annotation

    def _read_value_bindings(self, tree: ast.AST) -> None:
        """Propagate constructor, factory, annotation, and simple alias values."""
        nodes = list(ast.walk(tree))
        for _ in range(len(nodes) + 1):
            changed = False
            for node in nodes:
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
                for target, value in pairs:
                    name = _target_name(target)
                    resolved = self._value_result(value)
                    if name and resolved and self.values.get(name) != resolved:
                        self.values[name] = resolved
                        changed = True
            if not changed:
                break

    def _value_result(self, node: ast.AST | None) -> str | None:
        """Return the transport identity produced by an expression."""
        if node is None:
            return None
        resolved = self.resolve(node)
        if isinstance(node, ast.Call):
            called = self.resolve(node.func)
            if called in _CLIENT_METHODS:
                return called
            if called in _FACTORY_RESULTS:
                return _FACTORY_RESULTS[called]
            if called:
                return self.returns.get(called.rsplit(".", 1)[-1])
        if (resolved in _CLIENT_METHODS
                or resolved in _DIRECT_HTTP_FUNCTIONS
                or resolved in _HTTP_MODULES):
            return resolved
        return None

    def resolve(self, node: ast.AST | None) -> str | None:
        """Resolve a Name/Attribute/Call to its imported transport identity."""
        if isinstance(node, ast.Name):
            return self.values.get(node.id, self.aliases.get(node.id, node.id))
        if isinstance(node, ast.Attribute):
            base = self.resolve(node.value)
            if not base:
                return None
            dotted = f"{base}.{node.attr}"
            return self.values.get(dotted, dotted)
        if isinstance(node, ast.Call):
            called = self.resolve(node.func)
            if called in _CLIENT_METHODS:
                return called
            if called in _FACTORY_RESULTS:
                return _FACTORY_RESULTS[called]
            if called:
                return self.returns.get(called.rsplit(".", 1)[-1])
        return None


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
    if resolved in _DIRECT_HTTP_FUNCTIONS:
        return resolved
    if isinstance(node, ast.Attribute):
        client = bindings.resolve(node.value)
        methods = _CLIENT_METHODS.get(client or "", set())
        if node.attr in methods:
            return f"{client}.{node.attr}"
    return None


def _walk(
        node: ast.AST,
        traced: bool,
        path: str,
        out: list[str],
        bindings: _Bindings | None = None,
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
    :param bindings: module-level import and client binding resolver.
    :return: None; ``out`` is mutated in place.
    """
    if bindings is None:
        bindings = _Bindings(node)
    if _http_symbol(node, bindings) and not traced:
        out.append(f"{path}:{node.lineno}")
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        fn_traced = traced or _calls_wrapper(node)
        for child in ast.iter_child_nodes(node):
            _walk(child, fn_traced, path, out, bindings)
        return
    if isinstance(node, (ast.With, ast.AsyncWith)):
        body_traced = traced or any(_is_span_with(it) for it in node.items)
        for it in node.items:
            _walk(it.context_expr, traced, path, out, bindings)
        for child in node.body:
            _walk(child, body_traced, path, out, bindings)
        return
    for child in ast.iter_child_nodes(node):
        _walk(child, traced, path, out, bindings)


def _violations() -> list[str]:
    """Return ``path:line`` for every un-spanned direct HTTP reference.

    :return: sorted ``"path:line"`` strings.
    """
    out: list[str] = []
    files = subprocess.check_output(
        ["git", "ls-files", "redfish_ctl/*.py", "redfish_ctl/**/*.py"]).decode().split()
    for f in files:
        if f.endswith(("config.py", "telemetry/exporter.py", "telemetry/tracing.py")):
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
