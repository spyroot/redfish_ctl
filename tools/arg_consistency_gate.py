"""Gate: one spelling per CLI concept — no --log AND --logging.

A concept must use a single flag spelling across all commands. The concrete,
detectable violation is the same concept appearing in two spellings that differ
only by dash-vs-underscore (``--event-type`` and ``--event_type``) or by an
obvious synonym pair. This gate flags that, so a new command can't introduce a
second spelling of a flag that already exists.

    python3 tools/arg_consistency_gate.py

It does NOT force a global dash-or-underscore convention (``--idrac_ip`` is
deliberately underscore per the config contract) — it forbids a concept from
having BOTH. Ratchet: existing split pairs are grandfathered in
tools/arg_consistency_baseline.txt; a NEW split fails, and a fixed one must
leave the baseline.

Author Mus spyroot@gmail.com
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

_ADD = re.compile(r'add_argument\(\s*["\'](--[a-z0-9][a-z0-9_-]*)["\']')
_BASELINE = pathlib.Path(__file__).parent / "arg_consistency_baseline.txt"


def _flags() -> set[str]:
    """Return every CLI flag string declared in the package.

    :return: distinct ``--flag`` strings from add_argument calls.
    """
    flags: set[str] = set()
    files = subprocess.check_output(
        ["git", "ls-files", "redfish_ctl/*.py", "redfish_ctl/**/*.py"]).decode().split()
    for f in files:
        flags.update(_ADD.findall(pathlib.Path(f).read_text(encoding="utf-8")))
    return flags


def _split_pairs() -> list[str]:
    """Return canonical keys for concepts that exist in >1 spelling.

    Two flags collide when they are equal after lowercasing and unifying
    ``-``/``_`` — i.e. the same concept spelled two ways.

    :return: sorted ``"--a-b|--a_b"`` keys, one per colliding concept.
    """
    by_norm: dict[str, set[str]] = {}
    for fl in _flags():
        norm = fl[2:].replace("-", "_").lower()
        by_norm.setdefault(norm, set()).add(fl)
    return sorted("|".join(sorted(v)) for v in by_norm.values() if len(v) > 1)


def _baseline() -> set[str]:
    """Return grandfathered split keys.

    :return: the allowed existing ``"--a-b|--a_b"`` keys.
    """
    if not _BASELINE.exists():
        return set()
    return {ln.strip() for ln in _BASELINE.read_text().splitlines()
            if ln.strip() and not ln.startswith("#")}


def main() -> int:
    """Report new split-spelling concepts and stale baseline entries.

    :return: 0 when clean, 1 on a new split or a stale baseline entry.
    """
    base = _baseline()
    splits = set(_split_pairs())
    new = sorted(splits - base)
    stale = sorted(base - splits)
    for k in new:
        print(f"arg-consistency: {k} — one concept, two spellings; pick one "
              "(or baseline it in tools/arg_consistency_baseline.txt)")
    for k in stale:
        print(f"arg-consistency: {k} baselined but no longer split — "
              "remove it from the baseline (ratchet tightens)")
    if new or stale:
        print(f"arg-consistency: {len(new)} new split(s), {len(stale)} stale")
        return 1
    print("arg-consistency: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
