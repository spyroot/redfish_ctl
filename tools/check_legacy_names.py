#!/usr/bin/env python3
"""Fail when generic legacy names re-enter public source surfaces."""
from __future__ import annotations

import argparse
from pathlib import Path

FORBIDDEN = (
    "IDracManager",
    "idrac_manager",
    "idrac_shared",
    "IDRAC_API",
    "IDRAC_JSON",
    "IdracApiRespond",
    "IDRAC_IP",
    "IDRAC_USERNAME",
    "IDRAC_PASSWORD",
    "IDRAC_PORT",
    "IDRAC_HTTP",
    "IDRAC_DISCOVERY",
    "IDRAC_EXPORTER",
    "idrac_fixtures",
    "codex/rename-docs-",
    "/Users/spyroot/dev/idrac_ctl",
    "merge --no-ff",
)
DEFAULT_ROOTS = (
    "redfish_ctl",
    "tests",
    "docs",
    "README.md",
    "setup.py",
    "tools",
)
TEXT_SUFFIXES = {
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".rst",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
ALLOWLIST = {
    "tools/check_legacy_names.py",
    "tests/test_legacy_name_guard.py",
}


def _iter_files(root: Path, paths: tuple[str, ...]):
    for rel in paths:
        base = root / rel
        if not base.exists():
            continue
        if base.is_file():
            yield base
            continue
        for path in base.rglob("*"):
            if path.is_file():
                yield path


def _is_text_candidate(path: Path) -> bool:
    return path.suffix in TEXT_SUFFIXES or path.name in {"README", "Makefile"}


def scan(root: Path, paths: tuple[str, ...] = DEFAULT_ROOTS) -> list[tuple[Path, int, str, str]]:
    """Return legacy-name hits as ``(path, line, token, text)`` tuples."""
    hits = []
    for path in _iter_files(root, paths):
        rel = path.relative_to(root).as_posix()
        if rel in ALLOWLIST or not _is_text_candidate(path):
            continue
        try:
            lines = path.read_text(errors="ignore").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, start=1):
            for token in FORBIDDEN:
                if token in line:
                    hits.append((path, number, token, line.strip()))
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="repository root to scan")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    paths = DEFAULT_ROOTS if root == Path(".").resolve() else (".",)
    hits = scan(root, paths)
    for path, line, token, text in hits:
        print(f"{path.relative_to(root)}:{line}: {token}: {text}")
    if hits:
        print(f"legacy name guard failed: {len(hits)} hit(s)")
        return 1
    print("legacy name guard passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
