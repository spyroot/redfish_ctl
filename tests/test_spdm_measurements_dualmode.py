"""Dual-mode tests for SPDM signed measurements.

The Supermicro GB300 corpus exposes ComponentIntegrity leaves with
``#ComponentIntegrity.SPDMGetSignedMeasurements`` targets. These tests exercise
the real mock transport and assert the command discovers those targets instead
of hardcoding component ids.
"""
import json

import pytest

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.component_integrity.cmd_spdm_measurements import SpdmMeasurements
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_manager_shared import ApiRequestType

_BMC_RESOURCE = "/redfish/v1/ComponentIntegrity/HGX_ERoT_BMC_0"
_BMC_TARGET = (
    "/redfish/v1/ComponentIntegrity/HGX_ERoT_BMC_0/Actions/"
    "ComponentIntegrity.SPDMGetSignedMeasurements"
)
_NONCE = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


def _post_requests(redfish_service):
    """Return POST requests recorded by the mock Redfish service."""
    return [request for request in redfish_service.requests if request.method == "POST"]


def _replace_post_response(service, status_code, body):
    """Replace the mock POST handler with a fixed response."""
    requests_mock = pytest.importorskip("requests_mock")

    def post_cb(request, context):
        service.requests.append(request)
        context.status_code = status_code
        return json.dumps(body)

    service.mocker.post(requests_mock.ANY, text=post_cb)


def test_spdm_measurements_lists_targets_without_post(redfish_mock_factory):
    """No component argument lists SPDM-capable ComponentIntegrity resources."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.SpdmMeasurements,
        "spdm-measurements",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    targets = result.data["spdm_measurement_targets"]
    assert len(targets) == 14
    bmc = next(row for row in targets if row["Id"] == "HGX_ERoT_BMC_0")
    assert bmc["uri"] == _BMC_RESOURCE
    assert bmc["target"] == _BMC_TARGET
    assert bmc["TargetComponentURI"] == "/redfish/v1/Chassis/HGX_ERoT_BMC_0"
    assert _post_requests(service) == []


def test_spdm_measurements_follows_collection_pagination(redfish_mock_factory):
    """Target discovery follows Members@odata.nextLink pages."""
    manager, service = redfish_mock_factory("supermicro")
    service._overlay["/redfish/v1/componentintegrity"] = {
        "Members": [{"@odata.id": _BMC_RESOURCE}],
        "Members@odata.nextLink": "/redfish/v1/ComponentIntegrity/Page2",
    }
    service._overlay["/redfish/v1/componentintegrity/page2"] = {
        "Members": [
            {"@odata.id": "/redfish/v1/ComponentIntegrity/HGX_ERoT_CPU_0"}
        ],
    }

    result = manager.sync_invoke(
        ApiRequestType.SpdmMeasurements,
        "spdm-measurements",
    )

    assert result.error is None
    assert [row["Id"] for row in result.data["spdm_measurement_targets"]] == [
        "HGX_ERoT_BMC_0",
        "HGX_ERoT_CPU_0",
    ]
    assert _post_requests(service) == []


def test_spdm_measurements_reports_member_read_failure(redfish_mock_factory):
    """A failed required member read reports the failing URI instead of hiding it."""
    manager, service = redfish_mock_factory("supermicro")
    service._overlay["/redfish/v1/componentintegrity"] = {
        "Members": [{"@odata.id": "/redfish/v1/ComponentIntegrity/Missing"}],
    }

    with pytest.raises(InvalidArgument, match="resource not found"):
        manager.sync_invoke(
            ApiRequestType.SpdmMeasurements,
            "spdm-measurements",
        )

    assert _post_requests(service) == []


def test_spdm_measurements_reports_missing_collection(redfish_mock_factory):
    """A missing ComponentIntegrity collection reports the missing entry point."""
    manager, service = redfish_mock_factory("supermicro")
    service._overlay["/redfish/v1/componentintegrity"] = None

    with pytest.raises(InvalidArgument, match="resource not found"):
        manager.sync_invoke(
            ApiRequestType.SpdmMeasurements,
            "spdm-measurements",
        )

    assert _post_requests(service) == []


def test_spdm_measurements_dry_run_resolves_payload_without_post(
    redfish_mock_factory,
):
    """--dry_run resolves the action and payload, but sends no POST."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.SpdmMeasurements,
        "spdm-measurements",
        component="HGX_ERoT_BMC_0",
        measurement_indices=["0,1", "255"],
        nonce=_NONCE,
        slot_id=2,
        dry_run=True,
    )

    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == "#ComponentIntegrity.SPDMGetSignedMeasurements"
    assert result.data["level"] == "read_only"
    assert result.data["target"] == _BMC_TARGET
    assert result.data["payload"] == {
        "MeasurementIndices": [0, 1, 255],
        "Nonce": _NONCE,
        "SlotId": 2,
    }
    assert _post_requests(service) == []


def test_spdm_measurements_posts_read_only_action_by_default(
    redfish_mock_factory,
):
    """SPDM signed measurements are READ_ONLY, so no confirm flag is required."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.SpdmMeasurements,
        "spdm-measurements",
        component=_BMC_RESOURCE,
        measurement_indices=["255"],
        nonce=_NONCE,
    )

    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["level"] == "read_only"
    assert result.data["target"] == _BMC_TARGET

    posts = _post_requests(service)
    assert len(posts) == 1
    assert posts[0].path.lower() == _BMC_TARGET.lower()
    assert posts[0].json() == {
        "MeasurementIndices": [255],
        "Nonce": _NONCE,
    }


def test_spdm_measurements_accepts_sync_200_action_response(
    redfish_mock_factory,
):
    """A synchronous 200 action response is accepted as successful."""
    manager, service = redfish_mock_factory("supermicro")
    _replace_post_response(
        service,
        200,
        {"SignedMeasurements": [{"MeasurementIndex": 0}]},
    )

    result = manager.sync_invoke(
        ApiRequestType.SpdmMeasurements,
        "spdm-measurements",
        component=_BMC_RESOURCE,
    )

    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#ComponentIntegrity.SPDMGetSignedMeasurements"
    assert result.data["target"] == _BMC_TARGET
    assert result.data["level"] == "read_only"

    posts = _post_requests(service)
    assert len(posts) == 1
    assert posts[0].path.lower() == _BMC_TARGET.lower()


def test_spdm_measurements_unknown_component_raises(redfish_mock_factory):
    """Unknown component ids fail fast and do not POST."""
    manager, service = redfish_mock_factory("supermicro")

    with pytest.raises(InvalidArgument, match="no SPDM measurement target"):
        manager.sync_invoke(
            ApiRequestType.SpdmMeasurements,
            "spdm-measurements",
            component="missing-component",
        )

    assert _post_requests(service) == []


def test_spdm_measurements_rejects_invalid_measurement_index():
    """Measurement index parsing enforces the DMTF 0..255 range."""
    with pytest.raises(InvalidArgument, match="between 0 and 255"):
        SpdmMeasurements._parse_measurement_indices(["0", "256"])


def test_spdm_measurements_exposes_cli_entrypoint():
    """The spdm-measurements command is wired into the package registry."""
    registry = RedfishManagerBase().get_registry()
    assert registry[ApiRequestType.SpdmMeasurements]["spdm-measurements"] is (
        SpdmMeasurements
    )

    cmd_parser, cmd_name, cmd_help = SpdmMeasurements.register_subcommand(
        SpdmMeasurements
    )

    assert "--component" in cmd_parser.format_help()
    assert cmd_name == "spdm-measurements"
    assert "SPDM" in cmd_help
