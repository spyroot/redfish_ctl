#!/usr/bin/env python3
"""Enforce one connection: the endpoint/credential set is declared in ONE place.

The base manager ``IDracManager.__init__`` (redfish_ctl/idrac_manager.py) is the
single home for the connection parameters ``host, username, password, port,
is_http``. ``redfish_ctl/config.py`` is the single home for reading them from the
environment (``REDFISH_IP`` / ``REDFISH_PORT`` / ... with the legacy ``IDRAC_*``
fallback). Every command class inherits that constructor; a per-class
``__init__`` that only forwards ``*args, **kwargs`` to ``super`` adds nothing and
is the "duplicate args in every __init__" this gate removes.

Two checks, both AST-based (no import, no network, no credentials):

* ``redundant-init`` — a class ``__init__`` whose signature is exactly
  ``(self, *args, **kwargs)`` and whose body is only an optional docstring plus
  ``super(...).__init__(*args, **kwargs)``. It is pure boilerplate. ``--fix``
  deletes it so the class inherits the one base constructor.

* ``endpoint-arg`` — an ``__init__`` OUTSIDE the allowed base modules that
  declares a *parameter* named in the connection set (``host``, ``idrac_ip``,
  ``redfish_ip``, ``username``, ``password``, ``port``, and the vendor-prefixed
  variants). Declaring such a parameter is a second endpoint and fails the gate.
  Passing ``idrac_ip=...`` as a call keyword (e.g. fleet building one manager per
  node) is NOT a declaration and is not flagged.

Exit codes: 0 clean, 1 violations found (check mode), 2 usage error.

    tools/single_connection_gate.py            # check (default, read-only)
    tools/single_connection_gate.py --list     # list every pass-through found
    tools/single_connection_gate.py --fix      # remove redundant pass-throughs

Author Mus spyroot@gmail.com
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

# The connection set is declared ONLY here. Any other module declaring an
# __init__ parameter from ENDPOINT_PARAMS is introducing a second endpoint.
ALLOWED_ENDPOINT_HOMES = {
    "redfish_ctl/config.py",
    "redfish_ctl/idrac_manager.py",
    "redfish_ctl/redfish_manager.py",
}

ENDPOINT_PARAMS = {
    "host",
    "idrac_ip",
    "redfish_ip",
    "username",
    "idrac_username",
    "redfish_username",
    "password",
    "idrac_password",
    "redfish_password",
    "port",
    "idrac_port",
    "redfish_port",
}

REPO_ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOT = REPO_ROOT / "redfish_ctl"


def _is_super_init_call(node: ast.stmt) -> bool:
    """Return True when a statement is ``super(...).__init__(*args, **kwargs)``.

    :param node: a statement from a function body.
    :return: True when the statement forwards exactly ``*args, **kwargs`` to the
        base ``__init__`` via ``super`` (with or without explicit class/self
        arguments), else False.
    """
    if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
        return False
    call = node.value
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr != "__init__":
        return False
    inner = func.value
    if not isinstance(inner, ast.Call) or not isinstance(inner.func, ast.Name):
        return False
    if inner.func.id != "super":
        return False
    # exactly one positional *args and one **kwargs, nothing else
    if len(call.args) != 1 or not isinstance(call.args[0], ast.Starred):
        return False
    if not isinstance(call.args[0].value, ast.Name) or call.args[0].value.id != "args":
        return False
    if len(call.keywords) != 1 or call.keywords[0].arg is not None:
        return False
    kw_val = call.keywords[0].value
    return isinstance(kw_val, ast.Name) and kw_val.id == "kwargs"


def _is_passthrough_init(fn: ast.FunctionDef) -> bool:
    """Return True when a ``__init__`` is a pure ``*args, **kwargs`` pass-through.

    :param fn: a FunctionDef named ``__init__``.
    :return: True when the signature is exactly ``(self, *args, **kwargs)`` with
        no decorators/defaults and the body is only an optional docstring plus a
        single ``super(...).__init__(*args, **kwargs)`` call.
    """
    if fn.name != "__init__" or fn.decorator_list:
        return False
    a = fn.args
    if a.posonlyargs or a.kwonlyargs or a.defaults or a.kw_defaults:
        return False
    if len(a.args) != 1 or a.args[0].arg != "self":
        return False
    if a.vararg is None or a.vararg.arg != "args":
        return False
    if a.kwarg is None or a.kwarg.arg != "kwargs":
        return False
    body = list(fn.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]  # drop docstring
    return len(body) == 1 and _is_super_init_call(body[0])


def _endpoint_params(fn: ast.FunctionDef) -> list[str]:
    """Return the connection-set parameter names an ``__init__`` declares.

    :param fn: a FunctionDef named ``__init__``.
    :return: the sorted parameter names that fall in ``ENDPOINT_PARAMS`` (a
        second-endpoint declaration), empty when the constructor declares none.
    """
    names = set()
    a = fn.args
    for arg in [*a.posonlyargs, *a.args, *a.kwonlyargs]:
        if arg.arg in ENDPOINT_PARAMS:
            names.add(arg.arg)
    if a.vararg and a.vararg.arg in ENDPOINT_PARAMS:
        names.add(a.vararg.arg)
    if a.kwarg and a.kwarg.arg in ENDPOINT_PARAMS:
        names.add(a.kwarg.arg)
    return sorted(names)


def _is_manager_class(cls: ast.ClassDef) -> bool:
    """Return True when a class is in the BMC-manager hierarchy.

    The one-connection rule applies to classes that actually hold a BMC
    connection — managers and the command classes that subclass them. A plain
    request/data payload (e.g. ``TestNetworkShareReq``, whose ``host`` is a file
    share, not the BMC) is out of scope.

    :param cls: a ClassDef node.
    :return: True when any base name ends in ``Manager``, else False.
    """
    for base in cls.bases:
        name = base.attr if isinstance(base, ast.Attribute) else (
            base.id if isinstance(base, ast.Name) else "")
        if name.endswith("Manager"):
            return True
    return False


def _iter_init_methods(tree: ast.Module):
    """Yield every ``__init__`` FunctionDef defined directly on a class.

    :param tree: a parsed module AST.
    :yield: (ClassDef, FunctionDef) pairs for each ``__init__`` method.
    """
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        for item in cls.body:
            if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                yield cls, item


def _scan(path: Path):
    """Scan one module for redundant pass-throughs and endpoint-arg violations.

    :param path: absolute path to a ``.py`` module under ``redfish_ctl/``.
    :return: (passthroughs, endpoint_violations) where each entry is a
        (class_name, FunctionDef) tuple; endpoint violations exclude the allowed
        base homes.
    """
    rel = path.relative_to(REPO_ROOT).as_posix()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    passthroughs, endpoint = [], []
    for cls, fn in _iter_init_methods(tree):
        if _is_passthrough_init(fn):
            passthroughs.append((cls.name, fn))
        elif rel not in ALLOWED_ENDPOINT_HOMES and _is_manager_class(cls) \
                and _endpoint_params(fn):
            endpoint.append((cls.name, fn, _endpoint_params(fn)))
    return passthroughs, endpoint


def _delete_method(path: Path, fn: ast.FunctionDef) -> None:
    """Delete a method's source lines in place, trimming one trailing blank line.

    :param path: the module to edit.
    :param fn: the FunctionDef to remove (uses lineno/end_lineno).
    :return: None. Rewrites ``path`` with the method's line span removed.
    """
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    start = fn.lineno - 1          # 0-based, inclusive
    end = fn.end_lineno            # exclusive upper bound after conversion
    # swallow a single trailing blank line so no double blank remains
    if end < len(lines) and lines[end].strip() == "":
        end += 1
    del lines[start:end]
    path.write_text("".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Run the single-connection gate over ``redfish_ctl/``.

    :param argv: optional argument vector (defaults to ``sys.argv``).
    :return: process exit code — 0 clean, 1 violations in check mode.
    """
    ap = argparse.ArgumentParser(description="Enforce one connection: endpoint/"
                                 "credential params live in one place.")
    ap.add_argument("--fix", action="store_true",
                    help="remove redundant pass-through __init__ methods")
    ap.add_argument("--list", action="store_true",
                    help="list every pass-through __init__ found and exit 0")
    args = ap.parse_args(argv)

    all_pass, all_endpoint = [], []
    for path in sorted(SCAN_ROOT.rglob("*.py")):
        passthroughs, endpoint_hits = _scan(path)
        for cls_name, fn in passthroughs:
            all_pass.append((path, cls_name, fn))
        for cls_name, fn, names in endpoint_hits:
            all_endpoint.append((path, cls_name, fn, names))

    if args.list:
        for path, cls_name, fn in all_pass:
            print(f"{path.relative_to(REPO_ROOT)}:{fn.lineno}: {cls_name}.__init__ pass-through")
        print(f"# {len(all_pass)} pass-through __init__ methods")
        return 0

    # endpoint-arg violations are never auto-fixed: a second endpoint is a design
    # error a human must resolve.
    if all_endpoint:
        for path, cls_name, fn, names in all_endpoint:
            print(f"error: {path.relative_to(REPO_ROOT)}:{fn.lineno}: "
                  f"{cls_name}.__init__ declares endpoint params {names} — the "
                  f"connection set lives only in the base manager", file=sys.stderr)

    if args.fix:
        # delete bottom-up per file so earlier line numbers stay valid
        by_file: dict[Path, list[ast.FunctionDef]] = {}
        for path, _cls, fn in all_pass:
            by_file.setdefault(path, []).append(fn)
        for path, fns in by_file.items():
            for fn in sorted(fns, key=lambda f: f.lineno, reverse=True):
                _delete_method(path, fn)
        print(f"removed {len(all_pass)} pass-through __init__ methods from "
              f"{len(by_file)} files")
        return 1 if all_endpoint else 0

    if all_pass:
        for path, cls_name, fn in all_pass:
            print(f"error: {path.relative_to(REPO_ROOT)}:{fn.lineno}: "
                  f"{cls_name}.__init__ is a redundant pass-through — remove it "
                  f"(run --fix); the class inherits the one base constructor",
                  file=sys.stderr)
    total = len(all_pass) + len(all_endpoint)
    if total:
        print(f"single-connection: {total} violation(s)", file=sys.stderr)
        return 1
    print("single-connection: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
