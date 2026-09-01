"""Offline tests for the catalog-wide Splunk liveness gate."""
from pathlib import Path

import yaml

from tools import splunk_full_coverage_gate as gate

REPO_ROOT = Path(__file__).resolve().parents[2]


def _catalog(tmp_path, condition_gated=()):
    """Write a minimal catalog fixture.

    :param tmp_path: pytest temporary directory.
    :param condition_gated: metric names that may be quiet without failing.
    :return: fixture path.
    """
    path = tmp_path / "catalog.yaml"
    path.write_text(
        yaml.safe_dump({
            "version": 1,
            "canary_liveness": {
                "metric_prefix": "hw.",
                "default": "always_on",
                "condition_gated": list(condition_gated),
            },
            "metrics": [
                {"name": "hw.always", "kind": "gauge"},
                {"name": "hw.event", "kind": "gauge"},
                {"name": "hw.missing", "kind": "gauge"},
            ],
        }),
        encoding="utf-8",
    )
    return path


def _env(monkeypatch):
    """Set deterministic non-secret configuration for a gate test.

    :param monkeypatch: pytest monkeypatch fixture.
    """
    monkeypatch.setenv("SPLUNK_O11Y_REALM", "us1")
    monkeypatch.setenv("SPLUNK_API_TOKEN", "test-token")
    monkeypatch.delenv("SPLUNK_ACCESS_TOKEN", raising=False)


def test_active_series_passes_without_timestamp_fields(tmp_path, monkeypatch, capsys):
    """MTS active=true proves flow when update timestamps are absent."""
    _env(monkeypatch)
    catalog = _catalog(tmp_path)
    monkeypatch.setattr(
        gate,
        "query_metric",
        lambda realm, token, metric, timeout: {
            "count": 1,
            "active": 1,
            "active_complete": True,
            "newest_ms": 0,
        },
    )
    assert gate.run_gate(["--catalog", str(catalog)]) == 0
    out = capsys.readouterr().out
    assert out.count("PASS FLOWING") == 3
    assert "FLOWING=3 INACTIVE=0 MISSING=0 ERROR=0 TOTAL=3" in out


def test_always_on_missing_and_inactive_are_hard_failures(tmp_path, monkeypatch, capsys):
    """Missing or inactive always-on metrics make the scheduled gate non-green."""
    _env(monkeypatch)
    catalog = _catalog(tmp_path)

    def response(realm, token, metric, timeout):
        # One flowing, one inactive, and one missing metric.
        if metric == "hw.always":
            return {"count": 1, "active": 1, "active_complete": True,
                    "newest_ms": 0}
        if metric == "hw.event":
            return {"count": 2, "active": 0, "active_complete": True,
                    "newest_ms": 0}
        return {"count": 0, "active": 0, "active_complete": False,
                "newest_ms": 0}

    monkeypatch.setattr(gate, "query_metric", response)
    assert gate.run_gate(["--catalog", str(catalog)]) == 1
    out = capsys.readouterr().out
    assert "FAIL INACTIVE hw.event" in out
    assert "FAIL MISSING hw.missing" in out
    assert "FLOWING=1 INACTIVE=1 MISSING=1 ERROR=0 TOTAL=3" in out


def test_condition_gated_quiet_metrics_are_not_applicable(
        tmp_path, monkeypatch, capsys):
    """Quiet event metrics become explicit N/A evidence, not CI warnings."""
    _env(monkeypatch)
    catalog = _catalog(tmp_path, condition_gated=("hw.event", "hw.missing"))

    def response(realm, token, metric, timeout):
        # The hard metric flows while event-driven metrics remain quiet.
        if metric == "hw.always":
            return {"count": 1, "active": 1, "active_complete": True,
                    "newest_ms": 0}
        if metric == "hw.event":
            return {"count": 2, "active": 0, "active_complete": True,
                    "newest_ms": 0}
        return {"count": 0, "active": 0, "active_complete": False,
                "newest_ms": 0}

    monkeypatch.setattr(gate, "query_metric", response)
    assert gate.run_gate(["--catalog", str(catalog)]) == 0
    out = capsys.readouterr().out
    assert "N/A INACTIVE hw.event" in out
    assert "N/A MISSING hw.missing" in out
    assert "FLOWING=1 INACTIVE=1 MISSING=1 ERROR=0 TOTAL=3" in out
    assert "NOT_APPLICABLE=2 HARD_FAILURES=0" in out


def test_malformed_active_flag_fails_even_for_condition_gated_metric(
        tmp_path, monkeypatch, capsys):
    """Condition gating never converts an unverifiable API payload to success."""
    _env(monkeypatch)
    catalog = _catalog(tmp_path, condition_gated=("hw.event",))
    monkeypatch.setattr(
        gate,
        "query_metric",
        lambda realm, token, metric, timeout: {
            "count": 1,
            "active": 0,
            "active_complete": metric != "hw.event",
            "newest_ms": 0,
        },
    )
    assert gate.run_gate(["--catalog", str(catalog)]) == 1
    assert "FAIL ERROR hw.event" in capsys.readouterr().out


def test_dry_run_validates_real_catalog_without_credentials(monkeypatch, capsys):
    """Dry-run loads every static metric and performs no network query."""
    monkeypatch.delenv("SPLUNK_O11Y_REALM", raising=False)
    monkeypatch.delenv("SPLUNK_API_TOKEN", raising=False)
    monkeypatch.delenv("SPLUNK_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(
        gate,
        "query_metric",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("query called")),
    )
    assert gate.run_gate(["--dry-run"]) == 0
    policies = gate.load_catalog(gate.DEFAULT_CATALOGS)
    condition_gated = {row.name for row in policies if row.liveness == "condition_gated"}
    assert condition_gated == {
        "hw.component.last_reset_type",
        "hw.fabric.link_down_reason",
        "hw.power.edp_violation_state",
        "hw.power.break_performance_state",
    }
    assert len(policies) > len(condition_gated)
    assert len(policies) == 63
    assert "CONDITION_GATED=4" in capsys.readouterr().out


def test_scheduled_gate_is_required_and_uses_the_guarded_runner():
    """Registry and GitLab CI wire the full-coverage gate only to schedules."""
    registry = yaml.safe_load(
        (REPO_ROOT / "gates" / "manifest.yaml").read_text(encoding="utf-8"))
    row = next(item for item in registry["gates"]
               if item["id"] == "telemetry.full-coverage")
    assert row == {
        "id": "telemetry.full-coverage",
        "profile": "scheduled",
        "command": "scripts/gates/scheduled/telemetry-full-coverage.sh",
        "required": True,
        "mutates": False,
    }
    assert row["id"] in registry["mandatory_ids"]
    assert "gate-scheduled" in registry["required_jobs"]

    ci = yaml.safe_load((REPO_ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8"))
    job = ci["gate-scheduled"]
    assert "homelab-k8s" in job["tags"]
    assert job["script"] == ["./scripts/check.sh --profile scheduled"]
    assert job["rules"] == [
        {
            "if": (
                '$CI_COMMIT_REF_PROTECTED == "true" && '
                "$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH && "
                '$CI_PIPELINE_SOURCE == "schedule"'
            )
        }
    ]

    publish_rules = ci["publish-github"]["rules"]
    assert any(
        '$CI_PIPELINE_SOURCE != "schedule"' in rule.get("if", "")
        for rule in publish_rules
    )
