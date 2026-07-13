#!/usr/bin/env python3
"""Manage the committed Redfish corpus library.

The corpora are one Git-LFS ``.tar.gz`` per captured box under ``tests/`` (built
by ``tools/pack_corpus.py``), indexed by ``tests/corpus/manifest.json``. This CLI
is the single documented entry point for pulling every corpus and materializing
the JSON — see ``docs/corpus-library.md``.

Subcommands
-----------
list          Print the manifest (vendor, model, Redfish version, JSON count).
pull          ``git lfs pull`` the corpus tarballs (all, or --vendor/--model).
extract-all   Extract every corpus into ``<dest>/<vendor>_<model>/`` (full JSON
              tree for consumers such as the igc pipeline that need all files).
verify        Assert every tarball exists, is LFS-tracked, and its ``.json``
              count matches the manifest (bare LFS pointers are skipped, not
              failed, so the check works before ``git lfs pull``).

The module is also importable: :func:`load_manifest` returns the parsed rows and
:func:`resolve` maps ``(vendor, model)`` to a row, so callers can locate a corpus
by vendor/model instead of the raw capture-IP ``arcname``.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "tests" / "corpus" / "manifest.json"


def load_manifest(path: Path = MANIFEST_PATH) -> list[dict]:
    """Return the corpus rows from the manifest JSON."""
    data = json.loads(Path(path).read_text())
    return list(data.get("corpora", []))


def resolve(vendor: str, model: str, path: Path = MANIFEST_PATH) -> Optional[dict]:
    """Return the manifest row for ``(vendor, model)`` (case-insensitive), or None."""
    vendor, model = vendor.lower(), model.lower()
    for row in load_manifest(path):
        if row["vendor"].lower() == vendor and row["model"].lower() == model:
            return row
    return None


def _select(rows: list[dict], vendor: Optional[str], model: Optional[str]) -> list[dict]:
    """Filter rows by optional vendor and/or model (case-insensitive)."""
    out = rows
    if vendor:
        out = [r for r in out if r["vendor"].lower() == vendor.lower()]
    if model:
        out = [r for r in out if r["model"].lower() == model.lower()]
    return out


def _tarball_path(row: dict) -> Path:
    """Absolute path to a row's tarball."""
    return REPO_ROOT / row["tarball"]


def _is_lfs_pointer(path: Path) -> bool:
    """True if ``path`` is still a bare Git-LFS pointer (not yet ``git lfs pull``ed)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(120)
    except OSError:
        return False
    return head.startswith(b"version https://git-lfs.github.com/spec")


def _count_json(path: Path) -> int:
    """Count ``.json`` members in a corpus tarball."""
    with tarfile.open(path) as tar:
        return sum(1 for name in tar.getnames() if name.endswith(".json"))


def cmd_list(args: argparse.Namespace) -> int:
    """Print the manifest as a table."""
    rows = _select(load_manifest(), args.vendor, args.model)
    if not rows:
        print("no corpora match the filter", file=sys.stderr)
        return 1
    total = 0
    print(f"{'VENDOR':<11} {'MODEL':<12} {'REDFISH':<8} {'JSON':>6}  TARBALL")
    for row in rows:
        total += int(row["json_count"])
        print(f"{row['vendor']:<11} {row['model']:<12} "
              f"{row['redfish_version']:<8} {row['json_count']:>6}  {row['tarball']}")
    print(f"{'':<11} {'':<12} {'total':<8} {total:>6}")
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    """``git lfs pull`` the selected corpus tarballs."""
    rows = _select(load_manifest(), args.vendor, args.model)
    if not rows:
        print("no corpora match the filter", file=sys.stderr)
        return 1
    includes = ",".join(row["tarball"] for row in rows)
    cmd = ["git", "lfs", "pull", f"--include={includes}"]
    print("running:", " ".join(cmd))
    return subprocess.call(cmd, cwd=REPO_ROOT)


def cmd_extract_all(args: argparse.Namespace) -> int:
    """Extract selected corpora into ``<dest>/<vendor>_<model>/``."""
    rows = _select(load_manifest(), args.vendor, args.model)
    if not rows:
        print("no corpora match the filter", file=sys.stderr)
        return 1
    dest = Path(args.dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    pending = [r for r in rows if _is_lfs_pointer(_tarball_path(r))]
    if pending:
        names = ", ".join(f"{r['vendor']}/{r['model']}" for r in pending)
        print(f"error: not pulled (bare LFS pointer): {names}\n"
              f"run `python tools/corpus.py pull` first", file=sys.stderr)
        return 1
    for row in rows:
        out = dest / f"{row['vendor']}_{row['model']}"
        out.mkdir(parents=True, exist_ok=True)
        with tarfile.open(_tarball_path(row)) as tar:
            try:
                tar.extractall(out, filter="data")  # py3.12+ path-safe filter
            except TypeError:  # pragma: no cover - py<3.12 lacks the kwarg
                tar.extractall(out)
        print(f"extracted {row['json_count']:>5} json  {row['vendor']}/{row['model']} -> {out}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Check tarballs exist and their JSON counts match the manifest."""
    rows = _select(load_manifest(), args.vendor, args.model)
    ok = True
    for row in rows:
        path = _tarball_path(row)
        if not path.exists():
            print(f"MISSING  {row['tarball']}")
            ok = False
            continue
        if _is_lfs_pointer(path):
            print(f"pointer  {row['tarball']} (not pulled; skipped count check)")
            continue
        actual = _count_json(path)
        if actual != int(row["json_count"]):
            print(f"MISMATCH {row['tarball']}: manifest={row['json_count']} actual={actual}")
            ok = False
        else:
            print(f"ok       {row['tarball']} ({actual} json)")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    """Construct the argparse CLI."""
    parser = argparse.ArgumentParser(description="Manage the Redfish corpus library.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func, needs_dest in (
        ("list", cmd_list, False),
        ("pull", cmd_pull, False),
        ("extract-all", cmd_extract_all, True),
        ("verify", cmd_verify, False),
    ):
        p = sub.add_parser(name, help=func.__doc__.splitlines()[0])
        p.add_argument("--vendor", help="filter to one vendor (e.g. dell)")
        p.add_argument("--model", help="filter to one model (e.g. gb300)")
        if needs_dest:
            p.add_argument("--dest", required=True, help="destination directory")
        p.set_defaults(func=func)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point."""
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
