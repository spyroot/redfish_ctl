from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("contract_tool", ROOT / "tools" / "contract_tool.py")
assert SPEC and SPEC.loader
contract_tool = importlib.util.module_from_spec(SPEC)
sys.modules["contract_tool"] = contract_tool
SPEC.loader.exec_module(contract_tool)


def load_event_rules() -> dict[str, dict]:
    path = ROOT / "contracts" / "dmtf" / "dsp0266" / "1.24.0" / "services" / "eventing.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {rule["id"]: rule for rule in data["spec"]["rules"]}


def test_all_contracts_validate() -> None:
    documents = contract_tool.discover_documents()
    assert contract_tool.validate_documents(documents) == []


def test_document_shape_validation_rejects_unknown_top_level_field() -> None:
    document = contract_tool.LoadedDocument(
        path=Path("invalid.yaml"),
        data={
            "apiVersion": "redfish.semantics/v1alpha1",
            "kind": "ProtocolRuleSet",
            "metadata": {"name": "invalid"},
            "spec": {},
            "unexpected": True,
        },
    )

    errors = contract_tool.validate_documents([document])

    assert "invalid.yaml: unexpected top-level fields: ['unexpected']" in errors


def test_subscription_create_is_exact_201_with_location() -> None:
    rule = load_event_rules()["event.subscription.create.completed"]
    assert rule["expect"]["status"]["accept"] == {"matcher": "exact", "values": [201]}
    assert rule["expect"]["headers"]["Location"]["presence"] == "required"
    assert rule["expect"]["headers"]["Location"]["semantic"] == "created_subscription_resource_uri"


def test_event_receiver_accepts_any_2xx_but_prefers_204() -> None:
    rule = load_event_rules()["event.push.delivery-acknowledgement"]
    status = rule["expect"]["status"]
    assert status["accept"] == {"matcher": "class", "values": ["2xx"]}
    assert status["emitPreferred"] == 204
    assert status["examples"] == [200, 204]
    assert status["examplesExhaustive"] is False
    assert rule["expect"]["bodyByStatus"]["204"]["presence"] == "forbidden"


def test_unsupported_subscription_parameter_is_400_redfish_error() -> None:
    rule = load_event_rules()["event.subscription.create.unsupported-parameter"]
    assert rule["expect"]["status"]["emit"] == 400
    assert rule["expect"]["body"]["kind"] == "redfish_error"
    assert rule["expect"]["body"]["presence"] == "required"


def test_terminated_subscription_is_404() -> None:
    rule = load_event_rules()["event.subscription.request-after-termination"]
    assert rule["expect"]["status"]["emit"] == 404


def test_oem_overlay_never_changes_strict_emission() -> None:
    overlay_path = ROOT / "contracts" / "oem" / "example-vendor" / "example-bmc" / "4.2.yaml"
    overlay = yaml.safe_load(overlay_path.read_text(encoding="utf-8"))
    observation = overlay["spec"]["observations"][0]
    assert observation["classification"] == "known_dmtf_deviation"
    assert observation["behavior"]["strictDmtfMode"] == {"accept": False, "emit": False}
    assert observation["behavior"]["oemCompatibilityMode"]["accept"] is True
    assert observation["behavior"]["oemCompatibilityMode"]["emit"] is False


def test_generated_matrix_contains_eventing_rule() -> None:
    matrix = json.loads((ROOT / "generated" / "dmtf-effective-matrix.json").read_text(encoding="utf-8"))
    rows = {row["id"]: row for row in matrix["rows"]}
    assert rows["event.subscription.create.completed"]["accepted_status"] == "exact:201"
    assert rows["event.push.delivery-acknowledgement"]["accepted_status"] == "class:2xx"


def test_all_generated_artifacts_are_current() -> None:
    outputs = contract_tool.render_generated(contract_tool.discover_documents())
    for path, expected in outputs.items():
        assert path.read_text(encoding="utf-8") == expected


def test_classifier_strict_and_oem_modes() -> None:
    standard = contract_tool.classify(
        ROOT / "examples" / "observations" / "subscription-create-201.yaml",
        None,
    )
    assert standard["strictDmtf"] == "accepted"

    oem = contract_tool.classify(
        ROOT / "examples" / "observations" / "oem-subscription-create-200.yaml",
        ROOT / "contracts" / "oem" / "example-vendor" / "example-bmc" / "4.2.yaml",
    )
    assert oem["strictDmtf"] == "rejected"
    assert oem["oemCompatibility"] == "accepted_with_warning"


def test_classifier_requires_location_for_completed_create(tmp_path: Path) -> None:
    observation = yaml.safe_load(
        (ROOT / "examples" / "observations" / "subscription-create-201.yaml").read_text(
            encoding="utf-8"
        )
    )
    observation["response"]["headers"] = {}
    path = tmp_path / "missing-location.yaml"
    path.write_text(yaml.safe_dump(observation), encoding="utf-8")

    result = contract_tool.classify(path, None)

    assert result["strictDmtf"] == "rejected"
    assert result["acceptedByDmtfRules"] == []


def test_classifier_requires_active_subscription_for_delivery(tmp_path: Path) -> None:
    observation = {
        "operation": {
            "initiator": "redfish_service",
            "responder": "event_receiver",
            "method": "POST",
            "operationKind": "deliver_event",
            "target": {"relation": "subscription.Destination"},
        },
        "response": {"status": 204, "headers": {}},
    }
    path = tmp_path / "delivery-without-active-subscription.yaml"
    path.write_text(yaml.safe_dump(observation), encoding="utf-8")

    rejected = contract_tool.classify(path, None)
    observation["condition"] = {"subscriptionState": "active"}
    path.write_text(yaml.safe_dump(observation), encoding="utf-8")
    accepted = contract_tool.classify(path, None)

    rule_id = "event.push.delivery-acknowledgement"
    assert rule_id not in rejected["matchedRules"]
    assert rule_id in accepted["acceptedByDmtfRules"]
