#!/usr/bin/env python3
"""Bump the single-source version in ``redfish_ctl/version.py``.

Usage::

    python scripts/bump_version.py patch     # 1.1.1 -> 1.1.2
    python scripts/bump_version.py minor     # 1.1.1 -> 1.2.0
    python scripts/bump_version.py major     # 1.1.1 -> 2.0.0
    python scripts/bump_version.py --set 1.4.0
    python scripts/bump_version.py --show     # print current version, change nothing

Design (deliberately conservative so release automation never surprises anyone):

* It edits ONLY ``redfish_ctl/version.py`` — the single source of truth that
  ``setup.py`` reads and the CLI reports via ``--version``. There is nowhere else
  to keep in sync.
* It does NOT run git. It prints the exact tag+push commands so a human performs
  the release step deliberately.
* The CI release workflow refuses to publish unless the pushed ``vX.Y.Z`` tag
  matches this file, so a forgotten bump fails loudly instead of shipping a
  wrong or duplicate version.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

VERSION_FILE = Path(__file__).resolve().parent.parent / "redfish_ctl" / "version.py"
_VERSION_RE = re.compile(r"""__version__\s*=\s*['"]([^'"]+)['"]""")
_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def read_version() -> str:
    """Return the current ``__version__`` string from version.py."""
    m = _VERSION_RE.search(VERSION_FILE.read_text())
    if not m:
        sys.exit(f"error: no __version__ found in {VERSION_FILE}")
    return m.group(1)


def bump(current: str, part: str) -> str:
    """Return ``current`` with the given semver ``part`` incremented."""
    m = _SEMVER_RE.match(current)
    if not m:
        sys.exit(f"error: current version {current!r} is not X.Y.Z; use --set")
    major, minor, patch = (int(x) for x in m.groups())
    if part == "major":
        major, minor, patch = major + 1, 0, 0
    elif part == "minor":
        minor, patch = minor + 1, 0
    elif part == "patch":
        patch += 1
    else:  # pragma: no cover - argparse restricts choices
        sys.exit(f"error: unknown part {part!r}")
    return f"{major}.{minor}.{patch}"


def write_version(new: str) -> None:
    """Rewrite version.py to ``new`` (must be X.Y.Z)."""
    if not _SEMVER_RE.match(new):
        sys.exit(f"error: {new!r} is not a valid X.Y.Z version")
    text = VERSION_FILE.read_text()
    if not _VERSION_RE.search(text):
        sys.exit(f"error: no __version__ to replace in {VERSION_FILE}")
    VERSION_FILE.write_text(_VERSION_RE.sub(f"__version__ = '{new}'", text, count=1))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("part", nargs="?", choices=["patch", "minor", "major"],
                       help="which semver component to increment")
    group.add_argument("--set", dest="explicit", metavar="X.Y.Z",
                       help="set an explicit version instead of bumping")
    group.add_argument("--show", action="store_true", help="print current version and exit")
    args = parser.parse_args(argv)

    current = read_version()
    if args.show:
        print(current)
        return 0

    new = args.explicit if args.explicit else bump(current, args.part)
    if new == current:
        sys.exit(f"error: new version equals current ({current}); nothing to do")

    write_version(new)
    print(f"{current} -> {new}  (redfish_ctl/version.py)")
    print("\nNext steps (run deliberately):")
    print("  git add redfish_ctl/version.py")
    print(f"  git commit -m 'Release {new}'")
    print("  git push origin main")
    print(f"  git tag v{new} && git push origin v{new}   # CI builds + publishes on the tag")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
