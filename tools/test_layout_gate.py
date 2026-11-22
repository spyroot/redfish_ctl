"""Gate: a test lives in the domain dir that mirrors its subject, not flat.

The source layout is the map: ``redfish_ctl/<domain>/cmd_<verb>.py`` mirrors
``tests/<domain>/test_<verb>*.py``. A flat ``tests/test_*.py`` hides the
test's subject, which is how a test ends up asserting one vendor's wire
semantics (a Dell ``JID_`` job id) against another vendor's mock. Flat is
correct ONLY for tests of root-level modules (``redfish_manager.py``,
``idrac_manager.py``, ``redfish_main.py``, ...) and shared test
infrastructure — everything command-shaped belongs in its domain dir.

    python3 tools/test_layout_gate.py

Ratchet: the currently-flat files are grandfathered in
``tools/test_layout_baseline.txt``; a NEW flat test file fails the gate, and a
relocated one must leave the baseline. The goal is a baseline of root-module
and infrastructure tests only.

Author Mus spyroot@gmail.com
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

_BASELINE = pathlib.Path(__file__).parent / "test_layout_baseline.txt"


def _flat_tests() -> list[str]:
    """Return every tracked flat test file directly under ``tests/``.

    :return: sorted ``tests/<name>.py`` paths (no subdirectory components).
    """
    files = subprocess.check_output(
        ["git", "ls-files", "tests/*.py"]).decode().split()
    return sorted(f for f in files if f.count("/") == 1)


def _baseline() -> set[str]:
    """Return the grandfathered flat test paths.

    :return: the allowed pre-existing flat files.
    """
    if not _BASELINE.exists():
        return set()
    return {ln.strip() for ln in _BASELINE.read_text().splitlines()
            if ln.strip() and not ln.startswith("#")}


def main() -> int:
    """Report new flat test files and stale baseline entries.

    :return: 0 when clean, 1 on a new flat test or a stale baseline entry.
    """
    base = _baseline()
    flat = set(_flat_tests())
    new = sorted(flat - base)
    stale = sorted(base - flat)
    for f in new:
        print(f"test-layout: {f} — new flat test; put it in tests/<domain>/ "
              "mirroring the redfish_ctl/<domain>/ module it exercises "
              "(root-module/infra tests belong in the baseline instead)")
    for f in stale:
        print(f"test-layout: {f} baselined but no longer flat — remove it "
              "from the baseline (ratchet tightens)")
    if new or stale:
        print(f"test-layout: {len(new)} new, {len(stale)} stale")
        return 1
    print(f"test-layout: clean ({len(base)} flat files baselined)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
