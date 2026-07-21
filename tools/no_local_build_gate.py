"""Gate: no local docker/k8s builds — the toolbox pipeline does that.

"No ghosts": an agent must not build a docker image or mutate a cluster on the
operator's workstation. Those run in the internal GitLab pipeline on the shared
toolbox image (ci-toolbox.md) or are dispatched to the fleet by ssh. This gate
flags a committed Makefile recipe or script line that builds/mutates LOCALLY:

  - ``docker build`` NOT dispatched over ssh (a local image build)
  - ``kind create|delete`` (kind is always local)
  - ``kubectl apply|create|delete|patch|replace`` at a recipe/script line
    (a local cluster mutation — cluster work is dispatched, never from a laptop)

Ratchet: existing local-dev targets are grandfathered in
tools/no_local_build_baseline.txt with the intent of moving them to the
pipeline; the baseline may only shrink, and any NEW local build fails.

    python3 tools/no_local_build_gate.py

Author Mus spyroot@gmail.com
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

# A local build/mutation line. docker build preceded by ssh (remote fleet build)
# is allowed, so the ssh form is excluded by the negative lookbehind-ish split.
LOCAL_DOCKER_BUILD = re.compile(r"(?<!ssh )(?<!ssh )\bdocker\s+build\b")
KIND = re.compile(r"\bkind\s+(create|delete)\b")
KUBECTL_MUT = re.compile(r"\bkubectl\s+(apply|create|delete|patch|replace|scale)\b")

SCAN_GLOBS = ("Makefile", "*.sh", "*.mk")
EXEMPT = ("scripts/gates/",)


def _lines(path: pathlib.Path):
    """Yield (lineno, text) for a file, skipping comments and blanks.

    :param path: file to read.
    :yield: (1-indexed line number, stripped line) for non-comment lines.
    """
    for i, ln in enumerate(path.read_text(encoding="utf-8", errors="ignore")
                           .splitlines(), 1):
        s = ln.strip()
        if s and not s.startswith("#"):
            yield i, ln


def _files() -> list[str]:
    """Return tracked Makefile/shell files in scope.

    :return: repo-relative paths to scan.
    """
    out: list[str] = []
    for g in SCAN_GLOBS:
        out += subprocess.check_output(["git", "ls-files", g]).decode().split()
    return [f for f in sorted(set(out)) if not any(e in f for e in EXEMPT)]


def _baseline() -> set[str]:
    """Return grandfathered ``path:line-kind`` keys from the baseline file.

    :return: set of allowed local-build keys (existing debt).
    """
    p = pathlib.Path(__file__).parent / "no_local_build_baseline.txt"
    if not p.exists():
        return set()
    return {ln.strip() for ln in p.read_text().splitlines()
            if ln.strip() and not ln.startswith("#")}


def _findings() -> list[str]:
    """Find every local-build/mutation line as ``path:kind`` keys.

    :return: sorted ``path:kind`` keys (kind in docker-build/kind/kubectl).
    """
    hits: set[str] = []  # type: ignore[assignment]
    hits = set()
    for f in _files():
        p = pathlib.Path(f)
        for _n, ln in _lines(p):
            if "ssh " in ln:            # dispatched to a remote host — allowed
                continue
            if LOCAL_DOCKER_BUILD.search(ln):
                hits.add(f"{f}:docker-build")
            if KIND.search(ln):
                hits.add(f"{f}:kind")
            if KUBECTL_MUT.search(ln):
                hits.add(f"{f}:kubectl")
    return sorted(hits)


def main() -> int:
    """Run the ratchet and report.

    :return: 0 when clean, 1 on a new local build or a stale baseline entry.
    """
    base = _baseline()
    found = set(_findings())
    new = sorted(found - base)
    stale = sorted(base - found)
    for k in new:
        print(f"no-local-build: {k} builds/mutates locally — dispatch it to the "
              "pipeline/toolbox, or baseline it in tools/no_local_build_baseline.txt")
    for k in stale:
        print(f"no-local-build: {k} is baselined but gone — remove it "
              "(ratchet tightens)")
    if new or stale:
        print(f"no-local-build: {len(new)} new, {len(stale)} stale")
        return 1
    print("no-local-build: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
