"""Offline tests for the dry-run-contract ratchet gate.

The gate (tools/dry_run_gate.py) flags a mutable operational script that lacks
--dry-run and is not baselined, and flags a stale baseline entry (one that now
conforms or vanished). These tests drive its check() over synthetic trees so
the ratchet logic is proven without touching the real repo.

Author Mus spyroot@gmail.com
"""
import subprocess

import pytest

from tools import dry_run_gate as gate


def _git(tmp_path, *files):
    """Init a git repo with the given (relpath, content) files and stage them.

    :param tmp_path: pytest tmp_path.
    :param files: (relative-path, text) pairs to write and `git add`.
    :return: the repo root Path.
    """
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for rel, content in files:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return tmp_path


def _run_check(monkeypatch, tmp_path, baseline=""):
    """Run gate.check() as if the repo were tmp_path with the given baseline.

    :param monkeypatch: pytest monkeypatch.
    :param tmp_path: repo root created by _git.
    :param baseline: text for tools/dry_run_baseline.txt.
    :return: (new_violations, stale_baseline).
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gate, "_baseline", lambda: {
        ln.strip() for ln in baseline.splitlines()
        if ln.strip() and not ln.startswith("#")})
    return gate.check()


def test_mutable_without_dry_run_is_flagged(monkeypatch, tmp_path):
    """A new mutable script (docker build) with no --dry-run is a violation."""
    _git(tmp_path, ("scripts/deploy.sh", "#!/bin/sh\ndocker build -t x .\n"))
    new, stale = _run_check(monkeypatch, tmp_path)
    assert new == ["scripts/deploy.sh"] and stale == []


def test_mutable_with_dry_run_passes(monkeypatch, tmp_path):
    """A mutable script that handles --dry-run is clean."""
    _git(tmp_path, ("scripts/deploy.sh",
                    "#!/bin/sh\ncase $1 in --dry-run) d=1;; esac\ndocker build -t x .\n"))
    new, stale = _run_check(monkeypatch, tmp_path)
    assert new == [] and stale == []


def test_non_mutable_script_ignored(monkeypatch, tmp_path):
    """A read-only script is not subject to the contract."""
    _git(tmp_path, ("scripts/status.sh", "#!/bin/sh\ndocker ps\nkubectl get pods\n"))
    new, stale = _run_check(monkeypatch, tmp_path)
    assert new == [] and stale == []


@pytest.mark.parametrize("d", ["examples", "functional_test", "scripts/gates"])
def test_exempt_dirs_are_out_of_scope(monkeypatch, tmp_path, d):
    """examples/, functional_test/, and gate scripts are exempt even if mutable."""
    _git(tmp_path, (f"{d}/x.sh", "#!/bin/sh\nkubectl apply -f y.yaml\n"))
    new, stale = _run_check(monkeypatch, tmp_path)
    assert new == []


def test_baselined_violation_is_allowed(monkeypatch, tmp_path):
    """A grandfathered violation in the baseline does not fail the gate."""
    _git(tmp_path, ("scripts/old.sh", "#!/bin/sh\ndocker push x\n"))
    new, stale = _run_check(monkeypatch, tmp_path, baseline="scripts/old.sh")
    assert new == [] and stale == []


def test_baseline_entry_that_now_conforms_is_stale(monkeypatch, tmp_path):
    """When a baselined script gains --dry-run, the gate demands its removal
    from the baseline — the ratchet must tighten, never loosen."""
    _git(tmp_path, ("scripts/old.sh",
                    "#!/bin/sh\n[ \"$1\" = --dry-run ] && exit 0\ndocker push x\n"))
    new, stale = _run_check(monkeypatch, tmp_path, baseline="scripts/old.sh")
    assert stale == ["scripts/old.sh"]


def test_real_repo_gate_is_clean():
    """The shipped baseline covers the real repo — main() returns 0."""
    assert gate.main() == 0
