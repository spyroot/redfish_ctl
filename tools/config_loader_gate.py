"""Gate: environment is read in ONE loader, nowhere else.

Application code must receive canonical config from redfish_ctl/config.py, not
re-derive it from the environment at each call site. This gate forbids a raw env
read - ``os.getenv(...)``, ``os.environ[...]``/``.get``/``.setdefault``, or the
``env_first(...)`` primitive - anywhere outside the loader.

    python3 tools/config_loader_gate.py

Stronger than name-scanning (repo.no-ghost-env): that checks env-var *names*;
this forces *centralization* so there is exactly one place env is read. Ratchet:
existing scattered reads are grandfathered in tools/config_loader_baseline.txt;
a NEW read outside the loader fails, and a migrated one must leave the baseline.

Author Mus spyroot@gmail.com
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

# The loader - the only module allowed to read the environment.
_LOADER = "redfish_ctl/config.py"
# A raw env read. env_first is matched as a CALL (``env_first(``) so the
# re-export import line in redfish_shared.py is not flagged.
_READ = re.compile(r"os\.getenv\(|os\.environ\b|\benv_first\(")
_BASELINE = pathlib.Path(__file__).parent / "config_loader_baseline.txt"


def _violations() -> list[str]:
    """Return ``path:line`` for every raw env read outside the loader.

    :return: sorted ``"path:line"`` strings, one per offending source line.
    """
    out: list[str] = []
    files = subprocess.check_output(
        ["git", "ls-files", "redfish_ctl/*.py", "redfish_ctl/**/*.py"]).decode().split()
    for f in files:
        if f == _LOADER:
            continue
        for i, line in enumerate(pathlib.Path(f).read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if _READ.search(code):
                out.append(f"{f}:{i}")
    return sorted(out)


def _baseline() -> set[str]:
    """Return grandfathered ``path:line`` reads.

    :return: the allowed pre-existing offending locations.
    """
    if not _BASELINE.exists():
        return set()
    return {ln.strip() for ln in _BASELINE.read_text().splitlines()
            if ln.strip() and not ln.startswith("#")}


def main() -> int:
    """Report new out-of-loader env reads and stale baseline entries.

    :return: 0 when clean, 1 on a new read or a stale baseline entry.
    """
    base = _baseline()
    viol = set(_violations())
    new = sorted(viol - base)
    stale = sorted(base - viol)
    for v in new:
        print(f"config-loader: {v} - env read outside the loader; move it into "
              f"{_LOADER} and expose a config value")
    for v in stale:
        print(f"config-loader: {v} baselined but no longer an env read - "
              "remove it from the baseline (ratchet tightens)")
    if new or stale:
        print(f"config-loader: {len(new)} new, {len(stale)} stale")
        return 1
    print(f"config-loader: clean ({len(base)} reads baselined for migration)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
