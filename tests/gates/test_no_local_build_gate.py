"""Offline tests for the no-local-build ratchet gate.

The gate (tools/no_local_build_gate.py) flags a committed line that builds a
docker image or mutates a cluster locally, while allowing ssh-dispatched builds
and baselined debt. Driven over synthetic repos.

Author Mus spyroot@gmail.com
"""
import subprocess

import pytest

from tools import no_local_build_gate as gate


def _repo(tmp_path, rel, content):
    """Create a one-file git repo and stage it.

    :param tmp_path: pytest tmp_path.
    :param rel: file path relative to the repo root.
    :param content: file text.
    :return: repo root Path.
    """
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / rel).write_text(content)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return tmp_path


@pytest.mark.parametrize("line", [
    "\tdocker build -t x .",
    "\tkind create cluster",
    "\tkubectl apply -f y.yaml",
])
def test_local_build_flagged(monkeypatch, tmp_path, line):
    """A local docker build / kind / kubectl mutation is a finding."""
    _repo(tmp_path, "Makefile", f"target:\n{line}\n")
    monkeypatch.chdir(tmp_path)
    assert gate._findings()


def test_ssh_dispatched_build_allowed(monkeypatch, tmp_path):
    """A docker build dispatched over ssh (remote fleet) is NOT local."""
    _repo(tmp_path, "scripts/img.sh",
          "#!/bin/sh\nssh host 'docker build -t x -f Dockerfile -'\n")
    monkeypatch.chdir(tmp_path)
    assert gate._findings() == []


def test_read_only_kubectl_allowed(monkeypatch, tmp_path):
    """kubectl get/read is not a mutation, so it is not flagged."""
    _repo(tmp_path, "scripts/s.sh", "#!/bin/sh\nkubectl get pods\n")
    monkeypatch.chdir(tmp_path)
    assert gate._findings() == []


def test_gate_scripts_exempt(monkeypatch, tmp_path):
    """Gate implementations themselves are out of scope."""
    _repo(tmp_path, "scripts/gates/x.sh", "#!/bin/sh\ndocker build .\n")
    monkeypatch.chdir(tmp_path)
    assert gate._findings() == []


def test_real_repo_gate_is_clean():
    """The shipped baseline covers the real repo — main() returns 0."""
    assert gate.main() == 0
