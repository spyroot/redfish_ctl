"""Gate: no ghost environment variables.

Every environment variable the code reads must be declared in
tools/env_registry.txt. An agent cannot invent a new ``SOME_NEW_TIMEOUT`` or
resurrect a legacy name on the fly — a new variable is a deliberate registry
edit, reviewed, not a silent ghost. This keeps the env surface canonical
(REDFISH_* primary, IDRAC_* legacy fallback via env_first) instead of sprawling.

    python3 tools/no_ghost_env_gate.py

Ratchet: the registry is the allowed set. A read of an unregistered variable
fails; a registry entry that nothing reads is flagged stale, so the set stays
tight.

Author Mus spyroot@gmail.com
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

# Reads: os.environ["X"] / os.environ.get("X") / os.getenv("X") / env_first("A","B",...)
_READS = [
    re.compile(r'os\.environ\[\s*["\']([A-Z][A-Z0-9_]+)["\']'),
    re.compile(r'os\.environ\.get\(\s*["\']([A-Z][A-Z0-9_]+)["\']'),
    re.compile(r'os\.getenv\(\s*["\']([A-Z][A-Z0-9_]+)["\']'),
]
_ENV_FIRST = re.compile(r'env_first\(\s*((?:["\'][A-Z][A-Z0-9_]+["\']\s*,?\s*)+)')
_NAME = re.compile(r'["\']([A-Z][A-Z0-9_]+)["\']')
_REGISTRY = pathlib.Path(__file__).parent / "env_registry.txt"


def _registry() -> set[str]:
    """Return the declared env-var names from tools/env_registry.txt.

    :return: sanctioned env-var names (comment/blank lines ignored).
    """
    if not _REGISTRY.exists():
        return set()
    return {ln.strip() for ln in _REGISTRY.read_text().splitlines()
            if ln.strip() and not ln.startswith("#")}


def _read_names() -> set[str]:
    """Return every env-var name the redfish_ctl package reads.

    :return: distinct env-var names referenced in redfish_ctl/**.py.
    """
    files = subprocess.check_output(
        ["git", "ls-files", "redfish_ctl/*.py", "redfish_ctl/**/*.py"]).decode().split()
    names: set[str] = set()
    for f in files:
        t = pathlib.Path(f).read_text(encoding="utf-8")
        for pat in _READS:
            names.update(pat.findall(t))
        for m in _ENV_FIRST.finditer(t):
            names.update(_NAME.findall(m.group(1)))
    return names


def main() -> int:
    """Compare reads against the registry and report.

    :return: 0 when clean, 1 on a ghost (unregistered read) or a stale entry.
    """
    reg = _registry()
    read = _read_names()
    ghosts = sorted(read - reg)
    stale = sorted(reg - read)
    for g in ghosts:
        print(f"no-ghost-env: {g} is read but not in tools/env_registry.txt "
              "(declare it there, or remove the read)")
    for s in stale:
        print(f"no-ghost-env: {s} is registered but no longer read "
              "(remove it from tools/env_registry.txt)")
    if ghosts or stale:
        print(f"no-ghost-env: {len(ghosts)} ghost(s), {len(stale)} stale")
        return 1
    print("no-ghost-env: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
