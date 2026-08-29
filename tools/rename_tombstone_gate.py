"""Gate: renamed-away names stay gone — the old CLI name never comes back.

The tool was renamed ``idrac_ctl`` -> ``redfish_ctl`` (v1.1.0). The old name
survives ONLY as the sanctioned backward-compat alias surface (the shim
package, the packaging that publishes the alias, the alias tests, and the
history that records the rename). Everywhere else — docs, docstrings, code,
comments, fixtures — the old name is a resurrection: a reader learns a dead
name, an agent copies it forward, and the rename erodes one mention at a time.

    python3 tools/rename_tombstone_gate.py

Ratchet: every tracked file containing a renamed-away token is baselined in
``tools/rename_tombstone_baseline.txt`` as ``path:count``. A NEW file gaining
the token fails; a sanctioned file gaining MORE mentions fails; a cleanup that
removes mentions must shrink or drop the baseline row (the stale check makes
documentation-cleanup PRs tighten the ratchet automatically). The goal is a
baseline reduced to the alias surface alone.

Author Mus spyroot@gmail.com
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

# Renamed-away tokens. Add a row when another rename retires a public name.
_TOKENS = ("idrac_ctl",)

_BASELINE = pathlib.Path(__file__).parent / "rename_tombstone_baseline.txt"

# The gate and its baseline discuss the token by necessity; nothing else is
# exempt by name (the baseline is the only sanctioning mechanism).
_SELF = {
    "tools/rename_tombstone_gate.py",
    "tools/rename_tombstone_baseline.txt",
    "tests/gates/test_rename_tombstone_gate.py",
    "scripts/gates/repository/rename-tombstone.sh",
}


def _occurrences() -> dict[str, int]:
    """Count token occurrences per tracked text file.

    Binary files are skipped (``git grep -I``); the gate's own files are
    excluded so explaining the rule never violates it.

    :return: mapping of repo-relative path to total token count.
    """
    counts: dict[str, int] = {}
    for token in _TOKENS:
        out = subprocess.run(
            ["git", "grep", "-I", "-c", "--", token],
            capture_output=True, text=True, check=False).stdout
        for line in out.splitlines():
            path, _, n = line.rpartition(":")
            if not path or path in _SELF:
                continue
            counts[path] = counts.get(path, 0) + int(n)
    return counts


def _baseline() -> dict[str, int]:
    """Return the sanctioned ``path -> count`` rows.

    :return: grandfathered occurrence counts per file.
    """
    rows: dict[str, int] = {}
    if not _BASELINE.exists():
        return rows
    for line in _BASELINE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        path, _, n = line.rpartition(":")
        rows[path] = int(n)
    return rows


def main() -> int:
    """Report resurrected names, grown counts, and stale baseline rows.

    :return: 0 when clean, 1 on any new/grown occurrence or stale row.
    """
    base = _baseline()
    found = _occurrences()
    bad = False
    for path, n in sorted(found.items()):
        allowed = base.get(path)
        if allowed is None:
            print(f"rename-tombstone: {path} — the renamed-away name appears in "
                  f"a NEW file ({n}x); the tool is redfish_ctl (idrac_ctl is "
                  "only a compat alias) — use the new name")
            bad = True
        elif n > allowed:
            print(f"rename-tombstone: {path} — mentions grew {allowed} -> {n}; "
                  "new text must use the new name")
            bad = True
    for path, allowed in sorted(base.items()):
        n = found.get(path, 0)
        if n < allowed:
            print(f"rename-tombstone: {path} — baselined {allowed} but found "
                  f"{n}; shrink the baseline row (ratchet tightens)")
            bad = True
    if bad:
        return 1
    print(f"rename-tombstone: clean ({len(base)} files carry sanctioned "
          "alias/history mentions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
