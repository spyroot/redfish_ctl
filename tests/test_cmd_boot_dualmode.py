"""Dual-mode test for the boot-source query command.

Runs offline by default against the mock service (using the iDRAC-shaped fixture
in tests/idrac_fixtures/), and against real hardware when IDRAC_IP is set. This is
the template for porting the remaining live-only command tests: invoke the command
through sync_invoke and assert the CommandResult shape.

Author Mus spyroot@gmail.com
"""
import json

import pytest

from idrac_ctl.cmd_exceptions import InvalidArgument
from idrac_ctl.compute.cmd_power_state import RebootHost
from idrac_ctl.idrac_shared import IDRAC_API, ApiRequestType
from idrac_ctl.redfish_manager import CommandResult


def test_boot_query(redfish_api):
    """boot_query returns a JSON-serializable CommandResult from /BootSources."""
    result = redfish_api.sync_invoke(
        ApiRequestType.BootQuery, "boot_query"
    )
    assert isinstance(result, CommandResult)
    assert isinstance(result.data, dict)
    # the payload must be JSON-serializable (CLI renders it as JSON)
    json.dumps(result.data)
    # in mock mode the fixture carries the iDRAC boot-source attributes
    assert result.data["@odata.id"].endswith("/BootSources")


def test_current_boot_query_returns_boot_settings(redfish_api):
    """current_boot_query returns the ComputerSystem Boot object."""
    result = redfish_api.sync_invoke(
        ApiRequestType.CurrentBoot, "current_boot_query"
    )

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, dict)
    json.dumps(result.data)
    assert result.data["BootSourceOverrideEnabled"] in {
        "Disabled",
        "Once",
        "Continuous",
    }
    allowable_targets = result.data["BootSourceOverrideTarget@Redfish.AllowableValues"]
    assert isinstance(allowable_targets, list)
    assert "Pxe" in allowable_targets
    assert result.data["BootSourceOverrideTarget"] in allowable_targets


def test_boot_one_shot_patches_requested_target_in_mock_mode(
    redfish_mock, redfish_service
):
    """boot_one_shot validates the target and PATCHes the system Boot payload."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.BootOneShot,
        "boot_one_shot",
        device="Pxe",
    )

    assert isinstance(result, CommandResult)
    assert result.data["Status"] == "ok"
    request = redfish_service.last_request
    assert request.method == "PATCH"
    assert request.path.lower() == "/redfish/v1/systems/system.embedded.1"
    assert request.json() == {"Boot": {"BootSourceOverrideTarget": "Pxe"}}

    current = redfish_mock.sync_invoke(
        ApiRequestType.CurrentBoot, "current_boot_query"
    )
    assert current.data["BootSourceOverrideTarget"] == "Pxe"


def test_boot_one_shot_rejects_invalid_target_before_patch_in_mock_mode(
    redfish_mock, redfish_service
):
    """boot_one_shot rejects unsupported targets before mutating Boot settings."""
    with pytest.raises(InvalidArgument, match="Invalid boot device Tape"):
        redfish_mock.sync_invoke(
            ApiRequestType.BootOneShot,
            "boot_one_shot",
            device="Tape",
        )

    assert all(request.method != "PATCH" for request in redfish_service.requests)


def test_reboot_posts_reset_action_payload_in_mock_mode(
    redfish_mock, redfish_service, monkeypatch
):
    """reboot POSTs the requested ResetType and records the generated task."""
    task_state = {"TaskState": "Completed", "TaskStatus": "OK"}

    def fetch_task(self, task_id):
        assert task_id == redfish_service.JOB_ID
        return task_state

    monkeypatch.setattr(
        IDRAC_API, "COMPUTE_RESET", "/Actions/ComputerSystem.Reset", raising=False
    )
    monkeypatch.setattr(RebootHost, "fetch_task", fetch_task)

    result = redfish_mock.sync_invoke(
        ApiRequestType.ComputerSystemReset,
        "reboot",
        reset_type="PowerCycle",
    )

    assert isinstance(result, CommandResult)
    assert result.data["task_id"] == redfish_service.JOB_ID
    assert result.data["task_state"] == task_state
    request = redfish_service.last_request
    assert request.method == "POST"
    assert request.path.lower() == (
        "/redfish/v1/systems/system.embedded.1/actions/computersystem.reset"
    )
    assert request.json() == {"ResetType": "PowerCycle"}
