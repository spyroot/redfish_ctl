"""Freeze required protected external-service gate semantics."""

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = REPO_ROOT / "specs" / "ci" / "protected-external-gates.yaml"


def test_splunk_api_token_gate_requires_live_redacted_authentication():
    """Presence alone cannot pass an expiring shared-token gate."""
    contract = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
    gate = contract["gates"][0]

    assert contract["schema"] == "redfish_ctl.protected_external_gates/v1"
    assert contract["authorities"] == {
        "environment": (
            "specs/config/environment.yaml#application.secrets_and_config"
        ),
        "secret_binding_owner": "protected-private-provider",
    }
    assert contract["lifecycle"]["state"] == "required-pending-provider"
    assert contract["lifecycle"]["enforcement_active"] is False
    assert "the capability is registered in gates/manifest.yaml" in contract[
        "lifecycle"
    ]["activation_requires"]
    assert gate["id"] == "splunk.api-token.authenticated"
    assert gate["profile"] == "integration"
    assert gate["required"] is True
    assert gate["provider_capability"] == {
        "id": "shared.splunk-observability.auth-probe",
        "version": "v1",
        "required": True,
        "secret_binding": "splunk-observability",
        "exact_commit_evidence": True,
    }
    assert gate["inputs"]["token_key"] == "SPLUNK_API_TOKEN"
    assert gate["inputs"]["token_value_output"] == "forbidden"
    assert gate["probe"]["success"]["http_status"] == 200
    assert gate["probe"]["success"]["body"]["zero_results"] == "allowed"
    failures = set(gate["probe"]["failures"])
    assert {
        "http-401-expired-revoked-or-invalid-token",
        "http-403-insufficient-scope",
        "timeout",
        "tls-error",
        "unexpected-json-shape",
    } <= failures
    assert {"token", "responseBody"} <= set(
        gate["evidence"]["forbidden_fields"]
    )
