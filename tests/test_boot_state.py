"""Offline test for boot-state — infer what the host will boot.

Synthesizes System.Boot + BootOptions + VirtualMedia into a boot/OS-state view.
Verified on the GB300 corpus (full BootOptions) and HPE iLO (boot mode/order).
"""
from redfish_ctl.idrac_shared import ApiRequestType


def test_boot_state_supermicro(redfish_mock_factory):
    """boot-state resolves boot order, bootable entries, and next boot on GB300."""
    mgr, _ = redfish_mock_factory("supermicro")
    state = mgr.sync_invoke(ApiRequestType.BootState, "boot-state").data
    assert isinstance(state, dict)
    assert state["System"] == "System_0"
    assert state["BootOrder"], "no boot order"
    assert state["BootableEntries"], "no BootOptions resolved"
    # next boot is inferred (override target or first in order)
    assert "NextBoot" in state and "OneTimeBootPending" in state
    assert isinstance(state["MountedMedia"], list)


def test_boot_state_ilo(redfish_mock_factory):
    """boot-state reports mode + order on iLO (vendor-neutral)."""
    mgr, _ = redfish_mock_factory("hpe")
    state = mgr.sync_invoke(ApiRequestType.BootState, "boot-state").data
    assert isinstance(state, dict)
    assert state["BootMode"] == "UEFI"
    assert state["Override"] == "Disabled"
    assert state["OneTimeBootPending"] is False


def test_boot_state_reports_mounted_media(redfish_mock_factory):
    """MountedMedia lists only inserted virtual media (none inserted in the corpus)."""
    mgr, _ = redfish_mock_factory("supermicro")
    state = mgr.sync_invoke(ApiRequestType.BootState, "boot-state").data
    assert all(m.get("Device") for m in state["MountedMedia"])
