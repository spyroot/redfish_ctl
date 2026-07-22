"""Unit tests for the nic-firmware command's classification and resilience.

Covers the review-hardened behavior: token-based (not substring) classification,
Dell Current-/Installed- firmware de-duplication, and graceful degradation when
the top-level Chassis collection errors (the firmware slice must still land).
"""

from __future__ import annotations

import json

import pytest

from redfish_ctl.network.cmd_nic_firmware import network_class
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType


@pytest.mark.parametrize(
    "text, expected",
    [
        ("CX8_0", "NIC"),
        ("NIC_1", "NIC"),
        ("Current-110618-21.5.9__NIC.Slot.1", "NIC"),
        ("Riser_Slot2_BlueField_3_Card", "DPU"),
        ("BF3_0", "DPU"),
        ("ConnectX-8 800GE 2P NIC", "NIC"),
        # Not network firmware — must return None (no substring false-positives).
        ("HGX_FW_GPU_1", None),
        ("AB86B73D_8D56_41E4_971E_B11DB71F2E33", None),
        ("BIOS", None),
        ("", None),
        (None, None),
    ],
)
def test_network_class_token_matching(text, expected):
    """Classification matches whole tokens, so GUIDs/GPU firmware are not network."""
    assert network_class(text) == expected


def _serve(routes):
    """Return a requests_mock text callback that serves ``routes`` (path -> obj/int).

    requests_mock lowercases the request path, so routes are matched case-insensitively.
    """
    lowered = {path.lower(): entry for path, entry in routes.items()}

    def cb(request, context):
        entry = lowered.get(request.path.lower())
        if entry is None:
            context.status_code = 404
            return json.dumps({"error": "not found"})
        if isinstance(entry, int):
            context.status_code = entry
            return json.dumps({"error": "status"})
        context.status_code = 200
        return json.dumps(entry)
    return cb


def _run_nic_firmware(routes):
    requests_mock = pytest.importorskip("requests_mock")
    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=_serve(routes))
        mgr = IDracManager(idrac_ip="mock", idrac_username="root", idrac_password="x",
                                 insecure=True, is_debug=False)
        return mgr.sync_invoke(ApiRequestType.NicFirmware, "nic-firmware").data


def test_dedups_dell_current_installed_pairs():
    """Paired Current-*/Installed-* firmware members collapse to one component."""
    fw_base = "/redfish/v1/UpdateService/FirmwareInventory"
    routes = {
        "/redfish/v1/Chassis": {"Members": []},  # no adapters needed for this case
        fw_base: {"Members": [
            {"@odata.id": f"{fw_base}/Current-21.5.9__NIC.Slot.1"},
            {"@odata.id": f"{fw_base}/Installed-21.5.9__NIC.Slot.1"},
        ]},
        f"{fw_base}/Current-21.5.9__NIC.Slot.1": {
            "Id": "Current-21.5.9__NIC.Slot.1", "Version": "21.5.9",
            "Updateable": True, "Name": "NIC.Slot.1"},
        f"{fw_base}/Installed-21.5.9__NIC.Slot.1": {
            "Id": "Installed-21.5.9__NIC.Slot.1", "Version": "21.5.9",
            "Updateable": False, "Name": "NIC.Slot.1"},
    }
    data = _run_nic_firmware(routes)
    assert data["summary"]["firmware_count"] == 1  # not 2
    assert data["summary"]["distinct_versions"] == ["21.5.9"]


def test_survives_chassis_error_and_still_returns_firmware():
    """A 500 on the Chassis collection must not sink the firmware read."""
    fw_base = "/redfish/v1/UpdateService/FirmwareInventory"
    routes = {
        "/redfish/v1/Chassis": 500,  # adapters walk fails
        fw_base: {"Members": [{"@odata.id": f"{fw_base}/CX8_0"}]},
        f"{fw_base}/CX8_0": {"Id": "CX8_0", "Version": "40.45.3048",
                             "Updateable": True, "Name": "Software Inventory"},
    }
    data = _run_nic_firmware(routes)
    assert data["adapters"] == []  # chassis walk degraded gracefully
    assert data["summary"]["firmware_count"] == 1
    assert any(f["Id"] == "CX8_0" and f["Version"] == "40.45.3048"
               for f in data["firmware"])
