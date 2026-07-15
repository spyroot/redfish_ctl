#!/usr/bin/env python3
"""Hard gate: every NEW or CHANGED function/method documents itself.

Diff-aware by design. It checks only the functions a change actually adds or
modifies (against a base ref), so it blocks new gaps immediately without forcing
a backfill of the whole tree first. For each in-scope function it requires:

  * a docstring (what it does),
  * a ``:param <name>:`` line for every named parameter (self/cls, ``*args`` and
    ``**kwargs`` are exempt), and
  * a ``:return:``/``:returns:`` line when the body returns a real value.

The style is the codebase's reStructuredText convention (see
``redfish_ctl/redfish_manager_base.py``). Run ``--all`` to report the whole-tree
backlog instead of the diff.

Exit code 1 (with a per-violation report) fails the gate; 0 passes.

Author Mus spyroot@gmail.com
"""
from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path

# Paths whose functions the gate covers. Tests keep a lighter "one line saying
# what it checks" convention and are intentionally excluded.
DEFAULT_ROOTS = ("redfish_ctl", "k8s")


def _git(*args: str) -> str:
    """Run a git command and return stdout (empty string on failure).

    :param args: git subcommand and arguments.
    :return: captured stdout, or "" if git exits non-zero.
    """
    res = subprocess.run(["git", *args], capture_output=True, text=True)
    return res.stdout if res.returncode == 0 else ""


def changed_files(base: str, roots: tuple[str, ...]) -> list[Path]:
    """List Python files under ``roots`` that differ from ``base``.

    :param base: git ref to diff against (e.g. ``origin/main``).
    :param roots: path prefixes to restrict the diff to.
    :return: sorted list of changed ``*.py`` paths that still exist on disk.
    """
    out = _git("diff", "--name-only", f"{base}...HEAD", "--", *roots)
    files = []
    for line in out.splitlines():
        p = Path(line.strip())
        if p.suffix == ".py" and p.exists() and "tests" not in p.parts:
            files.append(p)
    return sorted(files)


def added_lines(base: str, path: Path) -> set[int]:
    """Return the new-file line numbers that ``path`` adds or changes vs ``base``.

    :param base: git ref to diff against.
    :param path: file to inspect.
    :return: set of 1-based line numbers touched on the new side of the diff.
    """
    out = _git("diff", "--unified=0", f"{base}...HEAD", "--", str(path))
    lines: set[int] = set()
    new_ln = 0
    for row in out.splitlines():
        hunk = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", row)
        if hunk:
            new_ln = int(hunk.group(1))
            continue
        if row.startswith("+") and not row.startswith("+++"):
            lines.add(new_ln)
            new_ln += 1
        elif not row.startswith("-"):
            new_ln += 1
    return lines


def _returns_value(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether the function's own body returns a non-None value.

    Nested functions are ignored so an inner ``return`` does not force a
    ``:return:`` on the outer one.

    :param node: the function AST node.
    :return: True if a ``return <value>`` (not bare/None) appears in its body.
    """
    for child in ast.walk(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child is not node:
            continue
        if isinstance(child, ast.Return) and child.value is not None:
            if not (isinstance(child.value, ast.Constant) and child.value.value is None):
                return True
    return False


def _named_params(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Named parameters that must be documented (self/cls, *args, **kwargs exempt).

    :param node: the function AST node.
    :return: parameter names requiring a ``:param:`` line.
    """
    a = node.args
    names = [p.arg for p in (*a.posonlyargs, *a.args, *a.kwonlyargs)]
    return [n for n in names if n not in {"self", "cls"}]


def check_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Return the docstring problems for one function (empty list = compliant).

    :param node: the function AST node to check.
    :return: human-readable problem strings; empty when the function passes.
    """
    doc = ast.get_docstring(node)
    if not doc:
        return ["no docstring"]
    problems = []
    for name in _named_params(node):
        if not re.search(rf":param\s+{re.escape(name)}\s*:", doc):
            problems.append(f"param '{name}' not documented (:param {name}:)")
    if _returns_value(node) and node.name != "__init__" and not re.search(r":returns?:", doc):
        problems.append("return value not documented (:return:)")
    return problems


def scan(path: Path, scope: set[int] | None) -> list[tuple[int, str, list[str]]]:
    """Check functions in ``path`` (all, or only those overlapping ``scope``).

    :param path: Python file to parse.
    :param scope: new-side line numbers in scope; None means check every function.
    :return: list of (lineno, function name, problems) for non-compliant functions.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        end = getattr(node, "end_lineno", node.lineno)
        if scope is not None and scope.isdisjoint(range(node.lineno, end + 1)):
            continue
        problems = check_function(node)
        if problems:
            out.append((node.lineno, node.name, problems))
    return out


def main() -> int:
    """Entry point: scan the diff (or whole tree with --all) and report.

    :return: process exit code — 1 if any violation, else 0.
    """
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="origin/main", help="git ref to diff against")
    ap.add_argument("--all", action="store_true", help="check the whole tree, not the diff")
    ap.add_argument("--roots", nargs="*", default=list(DEFAULT_ROOTS))
    args = ap.parse_args()

    violations = 0
    if args.all:
        targets = [p for r in args.roots for p in Path(r).rglob("*.py") if "tests" not in p.parts]
        for path in sorted(targets):
            for ln, name, probs in scan(path, None):
                print(f"{path}:{ln} {name}() — {'; '.join(probs)}")
                violations += 1
    else:
        files = changed_files(args.base, tuple(args.roots))
        if not files:
            print("docstring-gate: no changed Python files to check.")
            return 0
        for path in files:
            for ln, name, probs in scan(path, added_lines(args.base, path)):
                print(f"{path}:{ln} {name}() — {'; '.join(probs)}")
                violations += 1

    if violations:
        print(f"\ndocstring-gate FAILED: {violations} function(s) need docs "
              f"(what it does + :param: each arg + :return:). See TEAM_GUIDE.md.")
        return 1
    print("docstring-gate: OK — all changed functions are documented.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
