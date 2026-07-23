"""Dual-mode tests for the one-time boot command (BootOneShot).

    redfish_ctl boot-one-shot --device Cd

Covers ``boot_one_shot`` (ApiRequestType.BootOneShot), a DMTF ComputerSystem
Boot-override PATCH. The preview (dry-run / confirm=False) paths mutate nothing,
so they run fully offline against the mock service and, when IDRAC_IP is set,
against real hardware without touching boot config.

Author Mus spyroot@gmail.com
"""
import pytest

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def test_boot_one_shot_dry_run_previews_nested_boot_payload(redfish_mock, redfish_service):
    """dry_run builds the nested {Boot: {...}} PATCH payload and writes nothing."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.BootOneShot,
        "boot_one_shot",
        device="Cd",
        dry_run=True,
    )
    assert isinstance(result, CommandResult)
    assert result.data["dry_run"] is True
    boot = result.data["payload"]["Boot"]
    assert boot["BootSourceOverrideTarget"] == "Cd"
    assert boot["BootSourceOverrideEnabled"] == "Once"
    # a preview never issues a PATCH
    assert all(r.method != "PATCH" for r in redfish_service.requests)


def test_boot_one_shot_confirm_false_blocks_write(redfish_mock, redfish_service):
    """confirm=False returns a blocked preview instead of PATCHing Boot."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.BootOneShot,
        "boot_one_shot",
        device="Cd",
        confirm=False,
    )
    assert result.data["dry_run"] is True
    assert result.data["blocked"] == "one-time boot requires confirm"
    assert all(r.method != "PATCH" for r in redfish_service.requests)


def test_boot_one_shot_rejects_unadvertised_target(redfish_mock):
    """A target the endpoint does not advertise is rejected before any PATCH."""
    with pytest.raises(InvalidArgument):
        redfish_mock.sync_invoke(
            ApiRequestType.BootOneShot,
            "boot_one_shot",
            device="NotARealDevice",
            dry_run=True,
        )


def test_boot_one_shot_maps_target_against_supermicro_boot(redfish_mock_factory):
    """boot-one-shot resolves Cd against the vendor's advertised Boot targets."""
    manager, _ = redfish_mock_factory("supermicro")
    result = manager.sync_invoke(
        ApiRequestType.BootOneShot,
        "boot_one_shot",
        device="Cd",
        dry_run=True,
    )
    assert result.data["dry_run"] is True
    assert result.data["payload"]["Boot"]["BootSourceOverrideTarget"] == "Cd"
