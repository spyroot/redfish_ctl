"""Dual-mode tests for the chassis-update command.

chassis-update PATCHes a Chassis resource from a JSON spec file. These tests
run offline against the mock Redfish service using a non-Dell (Supermicro)
fixture tree, asserting the exact PATCH the client sends and the guard rails
that reject an empty or missing spec before any write.
"""

import json

import pytest

from redfish_ctl.chassis.cmd_update_chassis import ChassisUpdate  # noqa: F401
from redfish_ctl.cmd_exceptions import InvalidArgument, InvalidArgumentFormat
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def _patch_requests(service):
    """Return only the PATCH requests recorded by the mock service.

    :param service: the backing ``MockRedfishService``.
    :return: list of recorded PATCH requests.
    """
    return [request for request in service.requests if request.method == "PATCH"]


def test_chassis_update_patches_spec_payload_to_member(
    redfish_mock_factory, tmp_path
):
    """chassis-update PATCHes the parsed spec body to the target Chassis member."""
    manager, service = redfish_mock_factory("supermicro")
    spec = tmp_path / "chassis.json"
    spec.write_text(json.dumps({"AssetTag": "lab-supermicro-01"}))

    result = manager.sync_invoke(
        ApiRequestType.ChassisUpdate,
        "update_chassis",
        chassis_id="Chassis_0",
        from_spec=str(spec),
    )

    assert isinstance(result, CommandResult)
    patches = _patch_requests(service)
    assert len(patches) == 1
    assert patches[0].path.lower() == "/redfish/v1/Chassis/Chassis_0".lower()
    assert patches[0].json() == {"AssetTag": "lab-supermicro-01"}


def test_chassis_update_rejects_empty_spec_path_without_patch(
    redfish_mock_factory,
):
    """chassis-update rejects an empty --from_spec before any PATCH is sent."""
    manager, service = redfish_mock_factory("supermicro")

    with pytest.raises(InvalidArgument, match="Invalid from_spec"):
        manager.sync_invoke(
            ApiRequestType.ChassisUpdate,
            "update_chassis",
            chassis_id="Chassis_0",
            from_spec="",
        )

    assert _patch_requests(service) == []


def test_chassis_update_rejects_empty_spec_body_without_patch(
    redfish_mock_factory, tmp_path
):
    """chassis-update rejects an empty JSON spec object before any PATCH is sent."""
    manager, service = redfish_mock_factory("supermicro")
    spec = tmp_path / "empty.json"
    spec.write_text("{}")

    with pytest.raises(InvalidArgumentFormat, match="empty spec"):
        manager.sync_invoke(
            ApiRequestType.ChassisUpdate,
            "update_chassis",
            chassis_id="Chassis_0",
            from_spec=str(spec),
        )

    assert _patch_requests(service) == []
