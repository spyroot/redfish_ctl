"""Offline test for the vendor-neutral oem-info command.

Surfaces vendor OEM extensions uniformly, closing the asymmetry where only Dell
OEM had commands. Verified on the HPE iLO (Oem.Hpe) and GB300 (Oem.Nvidia /
Oem.OpenBmc) corpora.
"""
from redfish_ctl.idrac_shared import ApiRequestType


def test_oem_info_surfaces_hpe(redfish_mock_factory):
    """oem-info reports the HPE OEM extension on an iLO host."""
    mgr, _ = redfish_mock_factory("hpe")
    result = mgr.sync_invoke(ApiRequestType.OemInfo, "oem-info")
    assert isinstance(result.data, list) and result.data
    vendors = {r["Vendor"] for r in result.data}
    assert "Hpe" in vendors


def test_oem_info_surfaces_nvidia_on_supermicro(redfish_mock_factory):
    """oem-info reports NVIDIA/OpenBMC OEM extensions on the GB300 manager."""
    mgr, _ = redfish_mock_factory("supermicro")
    result = mgr.sync_invoke(ApiRequestType.OemInfo, "oem-info")
    assert isinstance(result.data, list) and result.data
    vendors = {r["Vendor"] for r in result.data}
    assert "Nvidia" in vendors
    # each row identifies the resource and carries the OEM type/keys
    row = result.data[0]
    assert row["Resource"] and "Keys" in row
