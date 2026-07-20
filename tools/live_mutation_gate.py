"""Gate: live tests must mutate only through the round-trip helper.

Scans live-marked test modules (any module whose source contains
``pytest.mark.live``) and fails when one calls a mutating primitive —
``base_patch``, ``base_post``, ``base_delete``, or ``invoke_action`` —
directly instead of going through ``tests/live_utils.live_roundtrip``.
AST-based, so a mention inside a docstring or comment never trips it.

    python3 tools/live_mutation_gate.py [--tests-dir tests]

Exit 0 when clean; exit 1 listing file:line for every violation.

Author Mus spyroot@gmail.com
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import sys

MUTATING_CALLS = {"base_patch", "base_post", "base_delete", "invoke_action"}
HELPER_MODULE = "live_utils.py"


def find_violations(path: pathlib.Path) -> list[tuple[int, str]]:
    """Find direct mutating-primitive calls in one live test module.

    :param path: python file to scan.
    :return: (line, primitive-name) pairs; empty when the file is clean or
        is not a live-marked module.
    """
    source = path.read_text(encoding="utf-8")
    if "pytest.mark.live" not in source:
        return []
    tree = ast.parse(source, filename=str(path))
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in MUTATING_CALLS:
                hits.append((node.lineno, node.func.attr))
    return hits


def main(argv: list[str] | None = None) -> int:
    """Scan the tests tree and report violations.

    :param argv: optional argument vector for tests; defaults to sys.argv.
    :return: process exit code — 0 clean, 1 violations found.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tests-dir", default="tests", help="directory to scan")
    args = parser.parse_args(argv)

    bad = 0
    for path in sorted(pathlib.Path(args.tests_dir).rglob("*.py")):
        if path.name == HELPER_MODULE:
            continue
        for line, name in find_violations(path):
            print(f"{path}:{line}: live test calls {name}() directly — "
                  f"use tests/live_utils.live_roundtrip")
            bad += 1
    if bad:
        print(f"live-mutation-roundtrip: {bad} violation(s)")
        return 1
    print("live-mutation-roundtrip: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
