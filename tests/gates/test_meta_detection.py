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


def test_detects_job_using_extends(tmp_path, monkeypatch):
    """A job defined via extends is a failure, not a silent skip.

    The job loop used to skip anything without an inline script, so a job could inherit its tags,
    allow_failure and rules from a template and escape every pipeline check. Resolving extends needs a
    full GitLab resolver, so an unanalyzable job must fail rather than pass unexamined.
    """
    _write_gitlab(tmp_path, """
        gate-merge:
          extends: .base
          tags: [homelab-k8s]
    """)
    monkeypatch.setattr(gate_meta, "REPO_ROOT", tmp_path)
    failures, _ = gate_meta._check_gitlab(_valid_registry())
    assert any("extends" in f for f in failures)


def test_detects_job_using_trigger(tmp_path, monkeypatch):
    """A job that only triggers a child pipeline is a failure rather than an unchecked job."""
    _write_gitlab(tmp_path, """
        gate-merge:
          tags: [homelab-k8s]
          trigger:
            include: child.yml
    """)
    monkeypatch.setattr(gate_meta, "REPO_ROOT", tmp_path)
    failures, _ = gate_meta._check_gitlab(_valid_registry())
    assert any("trigger" in f for f in failures)


def test_detects_top_level_include(tmp_path, monkeypatch):
    """A top-level include makes the pipeline unanalyzable, so the meta-gate fails.

    Jobs defined in an included file are invisible here, meaning allow_failure or a missing runner tag
    could live entirely outside the file this gate reads.
    """
    _write_gitlab(tmp_path, """
        include:
          - local: other.yml
        gate-merge:
          tags: [homelab-k8s]
          script: [true]
    """)
    monkeypatch.setattr(gate_meta, "REPO_ROOT", tmp_path)
    failures, _ = gate_meta._check_gitlab(_valid_registry())
    assert any("include" in f for f in failures)


def test_detects_registry_without_required_jobs(tmp_path, monkeypatch):
    """An empty required_jobs list fails instead of silently disabling the required-job check."""
    _write_gitlab(tmp_path, """
        gate-merge:
          tags: [homelab-k8s]
          script: [true]
    """)
    monkeypatch.setattr(gate_meta, "REPO_ROOT", tmp_path)
    reg = _valid_registry()
    reg["required_jobs"] = []
    failures, _ = gate_meta._check_gitlab(reg)
    assert any("required_jobs" in f for f in failures)


def test_default_tags_satisfy_the_runner_tag(tmp_path, monkeypatch):
    """A job inheriting tags from `default:` is not reported as missing the runner tag.

    Treating every top-level key as a job would otherwise flag jobs that legitimately inherit tags,
    which is GitLab's own inheritance and resolvable without a resolver.
    """
    _write_gitlab(tmp_path, """
        default:
          tags: [homelab-k8s]
        gate-merge:
          script: [true]
    """)
    monkeypatch.setattr(gate_meta, "REPO_ROOT", tmp_path)
    failures, _ = gate_meta._check_gitlab(_valid_registry())
    assert not any("runner tag" in f for f in failures), failures


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
