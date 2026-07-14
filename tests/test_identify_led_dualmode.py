"""Dual-mode tests for the identify-led command."""

from pathlib import Path

import pytest

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def _mutating_requests(service):
    return [
        request
        for request in service.requests
        if request.method in {"POST", "PATCH", "DELETE"}
    ]


def _patch_requests(service):
    return [request for request in service.requests if request.method == "PATCH"]


def test_identify_led_reads_current_chassis_state_without_patch(redfish_mock_factory):
    """identify-led reads LocationIndicatorActive when no desired state is supplied."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.IdentifyLed,
        "identify-led",
        resource="chassis",
        target_id="Chassis_0",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == {
        "resource": "chassis",
        "target_id": "Chassis_0",
        "target": "/redfish/v1/Chassis/Chassis_0",
        "property": "LocationIndicatorActive",
        "current": False,
        "read_only": True,
    }
    assert _mutating_requests(service) == []


def test_identify_led_dry_run_previews_chassis_patch(redfish_mock_factory):
    """identify-led previews a LocationIndicatorActive PATCH unless confirmed."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.IdentifyLed,
        "identify-led",
        resource="chassis",
        target_id="Chassis_0",
        active=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["current"] is False
    assert result.data["payload"] == {"LocationIndicatorActive": True}
    assert result.data["target"] == "/redfish/v1/Chassis/Chassis_0"
    assert _mutating_requests(service) == []


def test_identify_led_confirm_patches_and_rereads_system_state(redfish_mock_factory):
    """identify-led --confirm PATCHes only the LED property and returns observed state."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.IdentifyLed,
        "identify-led",
        resource="system",
        target_id="System_0",
        active=True,
        confirm=True,
    )

    patches = _patch_requests(service)
    assert len(patches) == 1
    assert patches[0].path == "/redfish/v1/systems/system_0"
    assert patches[0].json() == {"LocationIndicatorActive": True}
    assert result.data["applied"] == {
        "target": "/redfish/v1/Systems/System_0",
        "status": "IdracApiRespond.Ok",
        "error": None,
    }
    assert result.data["observed"] is True


def test_identify_led_rejects_bad_targets_before_patch(redfish_mock_factory):
    """identify-led fails closed when the requested resource id is absent."""
    manager, service = redfish_mock_factory("supermicro")

    with pytest.raises(InvalidArgument, match="No chassis resource named Missing"):
        manager.sync_invoke(
            ApiRequestType.IdentifyLed,
            "identify-led",
            resource="chassis",
            target_id="Missing",
            active=True,
            confirm=True,
        )

    assert _mutating_requests(service) == []


def test_identify_led_collection_query_error_is_reported(monkeypatch):
    """Collection read failures report the BMC/query error, not a missing target."""
    from redfish_ctl.chassis.cmd_identify_led import IdentifyLed

    command = object.__new__(IdentifyLed)

    def fail_query(uri, do_async=False):
        return CommandResult(None, None, None, f"query failed for {uri}")

    monkeypatch.setattr(command, "base_query", fail_query)

    with pytest.raises(InvalidArgument, match="query failed for /redfish/v1/Chassis"):
        command._resolve("chassis", "Chassis_0", None, False)


def test_identify_led_live_scripts_use_portable_bash_shebang():
    """identify-led live scripts must not depend on a local Homebrew bash path."""
    repo_root = Path(__file__).resolve().parents[1]
    scripts = [
        repo_root / "scripts/live_sanity_check/hp/dl360/identify_led_roundtrip.sh",
        repo_root / "scripts/live_sanity_check/supermicro/gb300/identify_led_roundtrip.sh",
    ]

    for script in scripts:
        assert script.read_text(encoding="utf-8").splitlines()[0] == "#!/usr/bin/env bash"


def test_identify_led_indicatorled_payload_uses_legacy_values(redfish_mock_factory):
    """The legacy IndicatorLED property maps on/off to Lit/Off payload values."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.IdentifyLed,
        "identify-led",
        resource="chassis",
        target_id="Chassis_0",
        property_name="IndicatorLED",
        active=False,
    )

    assert result.data["payload"] == {"IndicatorLED": "Off"}
    assert result.data["current"] == "Off"
    assert _mutating_requests(service) == []
