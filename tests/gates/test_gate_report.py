"""Cover the sanitized gate-report generator.

Checks that the registry-only report lists every registered gate, that a results map is reflected,
and that a secret-shaped token is redacted before it can reach the artifact.
"""
from tools import gate_report


def test_registry_only_report_lists_gates():
    """A report with no results lists gates and says it is registry-only."""
    out = gate_report.build_report(None)
    assert "Gate report (sanitized)" in out
    assert "meta.gate-registry" in out
    assert "registry-only" in out


def test_results_are_reflected_and_skip_counts_as_fail_note():
    """A results map surfaces per-gate status and flags skipped gates as FAIL."""
    out = gate_report.build_report({"unit.all": "pass", "repo.shellcheck": "skip"})
    assert "| pass |" in out
    assert "treated as FAIL" in out
    assert "repo.shellcheck" in out


def test_secret_shaped_token_is_redacted():
    """A secret-shaped token in a rendered line is masked, never emitted verbatim."""
    line = "password: hunter2-supersecret"
    assert "hunter2" not in gate_report._redact(line)
    assert "[REDACTED]" in gate_report._redact(line)
