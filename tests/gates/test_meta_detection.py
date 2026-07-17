"""Prove the meta-gate DETECTS a broken registry or pipeline.

Each test deliberately removes, disables, or mis-registers a gate/job and asserts the meta-gate reports
a failure — so a silently missing, optional, unregistered, allow_failure, mis-tagged, or MR-reachable
live-apply gate can never slip through. Complements tests/test_gate_meta.py (the positive case).
"""
import textwrap

from tools import gate_meta


def _valid_registry():
    """Return a minimal schema-valid registry with one mandatory, required gate.

    :return: a registry dict the checks accept as consistent.
    """
    return {
        "version": 1,
        "runner_tag": "homelab-k8s",
        "required_jobs": ["gate-merge"],
        "mandatory_ids": ["repo.x"],
        "gates": [
            {"id": "repo.x", "profile": "merge", "command": "scripts/gates/repository/shellcheck.sh",
             "required": True, "mutates": False},
        ],
    }


def test_detects_missing_mandatory_id():
    """A mandatory ID absent from the registry is a failure."""
    reg = _valid_registry()
    reg["mandatory_ids"] = ["repo.x", "repo.absent"]
    failures = gate_meta._check_mandatory_ids(reg)
    assert any("repo.absent" in f for f in failures)


def test_detects_optional_mandatory_gate():
    """A mandatory gate registered as optional (required:false) is a failure."""
    reg = _valid_registry()
    reg["gates"][0]["required"] = False
    failures = gate_meta._check_mandatory_ids(reg)
    assert any("optional" in f for f in failures)


def test_detects_nonexistent_command():
    """A required gate whose command file does not exist is a failure."""
    reg = _valid_registry()
    reg["gates"][0]["command"] = "scripts/gates/repository/does-not-exist.sh"
    failures = gate_meta._check_commands(reg)
    assert any("does not exist" in f for f in failures)


def test_detects_unregistered_script(tmp_path, monkeypatch):
    """A gate script under scripts/gates/ that is not registered is a failure."""
    (tmp_path / "scripts" / "gates" / "repository").mkdir(parents=True)
    stray = tmp_path / "scripts" / "gates" / "repository" / "stray.sh"
    stray.write_text("#!/usr/bin/env bash\ntrue\n")
    monkeypatch.setattr(gate_meta, "REPO_ROOT", tmp_path)
    failures = gate_meta._check_no_unregistered_scripts(_valid_registry())
    assert any("stray.sh" in f for f in failures)


def _write_gitlab(tmp_path, body: str):
    """Write a .gitlab-ci.yml under a temp REPO_ROOT.

    :param tmp_path: pytest tmp dir used as REPO_ROOT.
    :param body: YAML body to write.
    """
    (tmp_path / ".gitlab-ci.yml").write_text(textwrap.dedent(body))


def test_detects_allow_failure(tmp_path, monkeypatch):
    """A GitLab job with allow_failure:true is a failure."""
    _write_gitlab(tmp_path, """
        gate-merge:
          tags: [homelab-k8s]
          allow_failure: true
          script: [./scripts/gates/run.sh merge]
    """)
    monkeypatch.setattr(gate_meta, "REPO_ROOT", tmp_path)
    failures, ran = gate_meta._check_gitlab(_valid_registry())
    assert ran and any("allow_failure" in f for f in failures)


def test_detects_missing_runner_tag(tmp_path, monkeypatch):
    """A GitLab job lacking the homelab-k8s tag is a failure."""
    _write_gitlab(tmp_path, """
        gate-merge:
          tags: [some-other]
          script: [./scripts/gates/run.sh merge]
    """)
    monkeypatch.setattr(gate_meta, "REPO_ROOT", tmp_path)
    failures, _ = gate_meta._check_gitlab(_valid_registry())
    assert any("runner tag" in f for f in failures)


def test_detects_live_apply_in_merge_request(tmp_path, monkeypatch):
    """A live-apply job reachable from a merge-request pipeline is a failure."""
    _write_gitlab(tmp_path, """
        deploy-apply:
          tags: [homelab-k8s]
          rules:
            - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
          script: [./scripts/gates/run.sh deploy]
    """)
    monkeypatch.setattr(gate_meta, "REPO_ROOT", tmp_path)
    failures, _ = gate_meta._check_gitlab(_valid_registry())
    assert any("merge-request" in f for f in failures)


def test_detects_missing_required_job(tmp_path, monkeypatch):
    """A required GitLab job that is absent is a failure."""
    _write_gitlab(tmp_path, """
        some-other-job:
          tags: [homelab-k8s]
          script: [true]
    """)
    monkeypatch.setattr(gate_meta, "REPO_ROOT", tmp_path)
    failures, _ = gate_meta._check_gitlab(_valid_registry())
    assert any("required GitLab job missing" in f for f in failures)
