"""Gate: every mutable operational script supports --dry-run.

Contract (TEAM_GUIDE "CI / Scripts"): a script that performs a mutating
operation must accept ``--dry-run``, and dry-run must not call mutating
commands. This gate enforces the first, statically-checkable half — a mutable
script must handle ``--dry-run`` — as a ratchet: existing violations are
grandfathered in tools/dry_run_baseline.txt, and any NEW or newly-mutating
script must conform, so the debt only shrinks.

    python3 tools/dry_run_gate.py

Scope: operational scripts. Illustrative examples/ (CLI demos) and functional_test/
(opt-in live-hardware tests, guarded by their own approval) are out of scope.

Author Mus spyroot@gmail.com
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

# A script is mutable if it issues a state-changing command.
MUTATING = re.compile(
    r"\bdocker\s+(build|run|push|rm|tag)\b"
    r"|\bkubectl\s+(apply|create|delete|patch|replace|scale)\b"
    r"|\bhelm\s+(install|upgrade|uninstall)\b"
    r"|\bkind\s+(create|delete)\b"
    r"|\bgit\s+push\b|\bscp\b|\bskopeo\s+copy\b"
    r"|\bssh\b.*\b(docker|rm|mkdir|tee|chmod)\b"
    r"|--apply\b|\brm\s+-rf\b",
    re.M,
)
DRY_RUN = re.compile(r"--dry-run|dry_run|dryrun|DRY_RUN")

# Out of scope: illustrative demos and opt-in live-hardware tests.
EXEMPT_DIRS = ("examples/", "functional_test/", "scripts/gates/")


def _scripts() -> list[str]:
    """Return tracked *.sh paths in scope (operational, non-exempt).

    :return: repo-relative shell-script paths the contract applies to.
    """
    out = subprocess.check_output(["git", "ls-files", "*.sh"]).decode().split()
    return [f for f in out if not f.startswith(EXEMPT_DIRS)
            and not any(d in f for d in EXEMPT_DIRS)]


def _baseline() -> set[str]:
    """Return the grandfathered violation set from the baseline file.

    :return: repo-relative paths allowed to violate (existing debt).
    """
    p = pathlib.Path(__file__).parent / "dry_run_baseline.txt"
    if not p.exists():
        return set()
    return {ln.strip() for ln in p.read_text().splitlines()
            if ln.strip() and not ln.startswith("#")}


def check() -> tuple[list[str], list[str]]:
    """Find contract violations and stale baseline entries.

    :return: (new_violations, stale_baseline) — new_violations are mutable
        in-scope scripts without --dry-run and not baselined; stale_baseline
        are baselined paths that now conform (or vanished) and should be
        removed so the ratchet tightens.
    """
    base = _baseline()
    new, conforming_baselined = [], []
    live = set(_scripts())
    for f in sorted(live):
        text = pathlib.Path(f).read_text(encoding="utf-8")
        if not MUTATING.search(text):
            continue
        if DRY_RUN.search(text):
            if f in base:
                conforming_baselined.append(f)
            continue
        if f not in base:
            new.append(f)
    stale = conforming_baselined + [f for f in base if f not in live]
    return new, stale


def main() -> int:
    """Run the ratchet check and report.

    :return: 0 when clean, 1 on a new violation or a stale baseline entry.
    """
    new, stale = check()
    for f in new:
        print(f"dry-run-contract: {f} mutates but has no --dry-run "
              "(add one, or baseline it in tools/dry_run_baseline.txt)")
    for f in stale:
        print(f"dry-run-contract: {f} is baselined but now conforms or is gone "
              "— remove it from tools/dry_run_baseline.txt (ratchet tightens)")
    if new or stale:
        print(f"dry-run-contract: {len(new)} new violation(s), "
              f"{len(stale)} stale baseline entr(y/ies)")
        return 1
    print("dry-run-contract: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
