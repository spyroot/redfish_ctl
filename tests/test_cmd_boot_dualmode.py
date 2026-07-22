"""Dual-mode test for the boot-source query command.

Runs offline by default against the mock service (using the iDRAC-shaped fixture
in tests/idrac_fixtures/), and against real hardware when IDRAC_IP is set. This is
the template for porting the remaining live-only command tests: invoke the command
through sync_invoke and assert the CommandResult shape.

Author Mus spyroot@gmail.com
"""
import copy
import json

import pytest

from redfish_ctl.boot_source.cmd_boot_one_shot import BootOneShot
from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.compute.cmd_power_state import RebootHost
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.idrac_shared import ApiRequestType


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


def test_boot_query_falls_back_to_system_boot_when_bootsources_missing(
    redfish_mock, redfish_service
):
    """boot_query falls back to standard ComputerSystem Boot when BootSources 404s."""
    boot_sources_key = "_redfish_v1_systems_system.embedded.1_bootsources.json"
    redfish_service._index = dict(redfish_service._index)
    assert boot_sources_key in redfish_service._index
    redfish_service._index.pop(boot_sources_key)

    result = redfish_mock.sync_invoke(
        ApiRequestType.BootQuery, "boot_query"
    )

    assert isinstance(result, CommandResult)
    assert result.discovered is None
    assert result.extra is None
    assert result.error is None
    assert isinstance(result.data, dict)
    json.dumps(result.data)
    assert result.data == {
        "BootSourceOverrideEnabled": "Disabled",
        "BootSourceOverrideTarget": "None",
        "BootSourceOverrideTarget@Redfish.AllowableValues": [
            "None",
            "Pxe",
            "Cd",
            "Usb",
            "Hdd",
            "BiosSetup",
            "Utilities",
            "Diags",
            "SDCard",
            "UefiTarget",
            "UefiHttp",
        ],
    }
    paths = [
        request.path.lower()
        for request in redfish_service.requests
    ]
    boot_sources_path = "/redfish/v1/systems/system.embedded.1/bootsources"
    system_path = "/redfish/v1/systems/system.embedded.1"
    boot_sources_idx = paths.index(boot_sources_path)
    assert system_path in paths[boot_sources_idx + 1:]
    assert all(request.method == "GET" for request in redfish_service.requests)


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


def test_boot_one_shot_dry_run_previews_payload_without_patch(
    redfish_mock, redfish_service
):
    """boot_one_shot dry_run reports the Boot payload without mutating settings."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.BootOneShot,
        "boot_one_shot",
        device="Pxe",
        mode="UEFI",
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == {
        "dry_run": True,
        "target": "/redfish/v1/Systems/System.Embedded.1",
        "payload": {
            "Boot": {
                "BootSourceOverrideEnabled": "Once",
                "BootSourceOverrideTarget": "Pxe",
                "BootSourceOverrideMode": "UEFI",
            }
        },
        "blocked": None,
    }
    assert redfish_service.requests
    assert all(request.method != "PATCH" for request in redfish_service.requests)


def test_boot_one_shot_dry_run_suppresses_power_on_and_reboot(
    redfish_mock, redfish_service
):
    """boot_one_shot dry_run does not fire nested power-on or reboot requests."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.BootOneShot,
        "boot_one_shot",
        device="Pxe",
        dry_run=True,
        do_power_on=True,
        do_reboot=True,
    )

    assert result.data["dry_run"] is True
    assert all(
        request.method not in {"PATCH", "POST"}
        for request in redfish_service.requests
    )


def test_boot_one_shot_confirm_false_previews_without_patch(
    redfish_mock, redfish_service
):
    """Internal callers can require explicit confirm before boot-one-shot writes."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.BootOneShot,
        "boot_one_shot",
        device="Pxe",
        confirm=False,
    )

    assert result.data["dry_run"] is True
    assert result.data["blocked"] == "one-time boot requires confirm"
    assert all(request.method != "PATCH" for request in redfish_service.requests)


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


def test_boot_one_shot_maps_cd_to_x10_advertised_target(redfish_mock_factory):
    """boot_one_shot maps generic Cd to X10 CD/DVD and sends a flat payload."""
    manager, service = redfish_mock_factory("supermicro_x10")

    result = manager.sync_invoke(
        ApiRequestType.BootOneShot,
        "boot_one_shot",
        device="Cd",
    )

    assert isinstance(result, CommandResult)
    assert result.data["Status"] == "ok"
    request = service.last_request
    assert request.method == "PATCH"
    assert request.path.lower() == "/redfish/v1/systems/1"
    assert request.json() == {
        "BootSourceOverrideEnabled": "Once",
        "BootSourceOverrideTarget": "CD/DVD",
    }


def test_boot_one_shot_x10_uefi_cd_uses_flat_payload_without_mode(
    redfish_mock_factory,
):
    """boot_one_shot sends X10 UefiCd without nested Boot or override mode."""
    manager, service = redfish_mock_factory("supermicro_x10")

    result = manager.sync_invoke(
        ApiRequestType.BootOneShot,
        "boot_one_shot",
        device="UefiCd",
        mode="UEFI",
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == {
        "dry_run": True,
        "target": "/redfish/v1/Systems/1",
        "payload": {
            "BootSourceOverrideEnabled": "Once",
            "BootSourceOverrideTarget": "UefiCd",
        },
        "blocked": None,
    }
    assert all(request.method != "PATCH" for request in service.requests)


def test_boot_one_shot_legacy_payload_strips_unsupported_uefi_fields():
    """X10 flat payloads omit fields unsupported by the legacy shape."""
    payload = BootOneShot._boot_payload(
        "UefiCd",
        "UEFI",
        "Boot0001",
        ["None", "CD/DVD", "UefiCd"],
        {
            "@odata.type": "#ComputerSystem.v1_3_0.ComputerSystem",
            "Manufacturer": "Supermicro",
        },
    )

    assert payload == {
        "BootSourceOverrideEnabled": "Once",
        "BootSourceOverrideTarget": "UefiCd",
    }


def test_boot_one_shot_non_legacy_cd_dvd_keeps_nested_payload(
    redfish_mock,
    redfish_service,
):
    """boot_one_shot keeps nested payloads when CD/DVD is not legacy X10."""
    system_path = "/redfish/v1/systems/system.embedded.1"
    system = copy.deepcopy(redfish_service._state(system_path))
    system["@odata.type"] = "#ComputerSystem.v1_18_0.ComputerSystem"
    system["Manufacturer"] = "Example Systems"
    system["Boot"]["BootSourceOverrideTarget@Redfish.AllowableValues"].append(
        "CD/DVD"
    )
    redfish_service._overlay[system_path] = system

    result = redfish_mock.sync_invoke(
        ApiRequestType.BootOneShot,
        "boot_one_shot",
        device="CD/DVD",
        mode="UEFI",
    )

    assert isinstance(result, CommandResult)
    assert result.data["Status"] == "ok"
    assert redfish_service.last_request.json() == {
        "Boot": {
            "BootSourceOverrideEnabled": "Once",
            "BootSourceOverrideTarget": "CD/DVD",
            "BootSourceOverrideMode": "UEFI",
        }
    }


def test_boot_one_shot_modern_uefi_cd_keeps_nested_payload(
    redfish_mock,
    redfish_service,
):
    """boot_one_shot keeps nested Boot payloads for modern UefiCd targets."""
    system_path = "/redfish/v1/systems/system.embedded.1"
    system = copy.deepcopy(redfish_service._state(system_path))
    system["Boot"]["BootSourceOverrideTarget@Redfish.AllowableValues"].append(
        "UefiCd"
    )
    redfish_service._overlay[system_path] = system

    result = redfish_mock.sync_invoke(
        ApiRequestType.BootOneShot,
        "boot_one_shot",
        device="UefiCd",
        mode="UEFI",
    )

    assert isinstance(result, CommandResult)
    assert result.data["Status"] == "ok"
    request = redfish_service.last_request
    assert request.method == "PATCH"
    assert request.path.lower() == "/redfish/v1/systems/system.embedded.1"
    assert request.json() == {
        "Boot": {
            "BootSourceOverrideEnabled": "Once",
            "BootSourceOverrideTarget": "UefiCd",
            "BootSourceOverrideMode": "UEFI",
        }
    }


def test_boot_one_shot_x10_power_on_failure_returns_error_before_patch(
    redfish_mock_factory,
):
    """boot_one_shot reports missing X10 chassis reset action before PATCHing."""
    manager, service = redfish_mock_factory("supermicro_x10")

    result = manager.sync_invoke(
        ApiRequestType.BootOneShot,
        "boot_one_shot",
        device="UefiCd",
        do_power_on=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "power-on pre-step failed: Failed to discover the reset chassis action"
    )
    assert result.data == {
        "target": "/redfish/v1/Systems/1",
        "payload": {
            "BootSourceOverrideEnabled": "Once",
            "BootSourceOverrideTarget": "UefiCd",
        },
    }
    assert all(request.method != "PATCH" for request in service.requests)


def test_boot_one_shot_x10_reboot_501_returns_error_after_patch(
    redfish_mock_factory,
):
    """boot_one_shot -r reports a chassis read failure instead of tracebacks."""
    requests_mock = pytest.importorskip("requests_mock")
    manager, service = redfish_mock_factory("supermicro_x10")
    original_get = service.get_cb

    def get_cb(request, context):
        if request.path.lower() == "/redfish/v1/chassis":
            service.requests.append(request)
            context.status_code = 501
            return "{}"
        return original_get(request, context)

    service.mocker.get(requests_mock.ANY, text=get_cb)

    result = manager.sync_invoke(
        ApiRequestType.BootOneShot,
        "boot_one_shot",
        device="UefiCd",
        do_reboot=True,
    )

    patches = [request for request in service.requests if request.method == "PATCH"]
    posts = [request for request in service.requests if request.method == "POST"]
    assert isinstance(result, CommandResult)
    # The 501 is normalized to a Redfish error envelope (contract), not a generic
    # "Failed acquire result" string; the reboot step still reports cleanly.
    assert result.error == "reboot post-step failed: Redfish error (HTTP 501)"
    assert result.data["Status"] == "ok"
    assert result.data["reboot_error"] == "Redfish error (HTTP 501)"
    assert len(patches) == 1
    assert patches[0].path.lower() == "/redfish/v1/systems/1"
    assert patches[0].json() == {
        "BootSourceOverrideEnabled": "Once",
        "BootSourceOverrideTarget": "UefiCd",
    }
    assert posts == []


def test_boot_one_shot_none_disarms_override_in_mock_mode(
    redfish_mock, redfish_service
):
    """boot_one_shot --device None disables the one-shot override."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.BootOneShot,
        "boot_one_shot",
        device="None",
    )

    assert isinstance(result, CommandResult)
    assert result.data["Status"] == "ok"
    request = redfish_service.last_request
    assert request.method == "PATCH"
    assert request.path.lower() == "/redfish/v1/systems/system.embedded.1"
    assert request.json() == {
        "Boot": {
            "BootSourceOverrideEnabled": "Disabled",
            "BootSourceOverrideTarget": "None",
        }
    }


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


def test_reboot_dry_run_previews_reset_action_without_post(
    redfish_mock, redfish_service
):
    """reboot --dry_run resolves ComputerSystem.Reset but sends no POST."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.ComputerSystemReset,
        "reboot",
        reset_type="GracefulRestart",
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == "#ComputerSystem.Reset"
    assert result.data["target"] == (
        "/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset"
    )
    assert result.data["payload"] == {"ResetType": "GracefulRestart"}
    assert result.data["level"] == "destructive"
    assert result.data["blocked"] is None
    assert all(request.method != "POST" for request in redfish_service.requests)


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
