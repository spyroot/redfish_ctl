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
from idrac_ctl.idrac_shared import ApiRequestType
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


def test_boot_state_command_dual_mode_returns_dell_offline_shape(
    redfish_api, redfish_service
):
    """boot-state synthesizes Dell System.Boot, BootOptions, and VirtualMedia."""
    result = redfish_api.sync_invoke(
        ApiRequestType.BootState, "boot-state"
    )

    assert isinstance(result, CommandResult)
    assert result.discovered is None
    assert result.extra is None
    assert result.error is None
    assert isinstance(result.data, dict)
    json.dumps(result.data)

    state = result.data
    assert set(state) == {
        "System",
        "BootMode",
        "Override",
        "OverrideTarget",
        "OneTimeBootPending",
        "NextBoot",
        "BootOrder",
        "BootableEntries",
        "MountedMedia",
    }
    assert state["System"] == "System.Embedded.1"
    assert state["Override"] == "Disabled"
    assert state["OverrideTarget"] == "None"
    assert state["OneTimeBootPending"] is False

    assert isinstance(state["BootOrder"], list)
    assert all(isinstance(entry, str) for entry in state["BootOrder"])
    if state["BootOrder"]:
        assert state["NextBoot"] == state["BootOrder"][0]
    else:
        assert state["NextBoot"] is None

    assert isinstance(state["BootableEntries"], list)
    bootable_entries = {
        entry["Ref"]: entry
        for entry in state["BootableEntries"]
    }
    assert bootable_entries == {
        "HardDisk.List.1-1": {
            "Ref": "HardDisk.List.1-1",
            "DisplayName": "Integrated RAID Controller 1",
            "Enabled": True,
        },
        "NIC.PxeDevice.1-1": {
            "Ref": "NIC.PxeDevice.1-1",
            "DisplayName": "Embedded NIC 1 Port 1 Partition 1",
            "Enabled": True,
        },
    }
    assert isinstance(state["MountedMedia"], list)
    assert all(set(media) == {"Device", "Image"} for media in state["MountedMedia"])
    assert redfish_service.requests
    assert all(request.method == "GET" for request in redfish_service.requests)


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
    assert request.json() == {
        "Boot": {
            "BootSourceOverrideEnabled": "Once",
            "BootSourceOverrideTarget": "Pxe",
        }
    }

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

    # reboot now discovers #ComputerSystem.Reset from the host system's Actions
    # block (the COMPUTE_RESET hardcode is gone), so only fetch_task is stubbed.
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


def test_boot_one_shot_uefi_mode_sets_override_mode_in_mock_mode(
    redfish_mock, redfish_service
):
    """boot_one_shot --mode UEFI adds BootSourceOverrideMode=UEFI to the PATCH."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.BootOneShot,
        "boot_one_shot",
        device="Cd",
        mode="UEFI",
    )

    assert isinstance(result, CommandResult)
    assert result.data["Status"] == "ok"
    request = redfish_service.last_request
    assert request.method == "PATCH"
    assert request.json() == {
        "Boot": {
            "BootSourceOverrideEnabled": "Once",
            "BootSourceOverrideTarget": "Cd",
            "BootSourceOverrideMode": "UEFI",
        }
    }


def test_boot_one_shot_legacy_mode_sets_override_mode_in_mock_mode(
    redfish_mock, redfish_service
):
    """boot_one_shot --mode Legacy adds BootSourceOverrideMode=Legacy to the PATCH."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.BootOneShot,
        "boot_one_shot",
        device="Cd",
        mode="Legacy",
    )

    assert isinstance(result, CommandResult)
    assert result.data["Status"] == "ok"
    assert redfish_service.last_request.json() == {
        "Boot": {
            "BootSourceOverrideEnabled": "Once",
            "BootSourceOverrideTarget": "Cd",
            "BootSourceOverrideMode": "Legacy",
        }
    }


def test_boot_one_shot_omits_mode_when_not_requested_in_mock_mode(
    redfish_mock, redfish_service
):
    """Without --mode the PATCH carries only the target (backward compatible)."""
    redfish_mock.sync_invoke(
        ApiRequestType.BootOneShot,
        "boot_one_shot",
        device="Cd",
    )
    boot = redfish_service.last_request.json()["Boot"]
    assert "BootSourceOverrideMode" not in boot
    assert boot["BootSourceOverrideTarget"] == "Cd"


def test_boot_one_shot_rejects_invalid_mode_before_patch_in_mock_mode(
    redfish_mock, redfish_service
):
    """boot_one_shot rejects an unsupported mode before mutating Boot settings."""
    with pytest.raises(InvalidArgument, match="Invalid boot mode"):
        redfish_mock.sync_invoke(
            ApiRequestType.BootOneShot,
            "boot_one_shot",
            device="Cd",
            mode="BIOS",
        )

    assert all(request.method != "PATCH" for request in redfish_service.requests)
