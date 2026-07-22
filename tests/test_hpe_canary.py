"""Opt-in live canary: idrac_ctl against a running HPE iLO emulator over HTTP.

Skipped by default (CI stays offline). To run it, start HPE's iLO Redfish
emulator (see examples/hpe_ilo_canary.sh) and export the connection:

    export HPE_EMULATOR_IP=127.0.0.1 HPE_EMULATOR_PORT=45678
    pytest -q tests/test_hpe_canary.py

This proves the commands work against a REAL HTTP Redfish service on a non-Dell
box — the wire-level complement to the offline tests/test_hpe_vendor.py.
"""
import os

import pytest

from redfish_ctl.idrac_shared import ApiRequestType

_IP = os.environ.get("HPE_EMULATOR_IP")

pytestmark = pytest.mark.skipif(
    not _IP, reason="set HPE_EMULATOR_IP (and run the iLO emulator) to enable")


def _manager():
    """Build an IDracManager pointed at the running emulator."""
    from redfish_ctl.idrac_manager import IDracManager
    return IDracManager(
        idrac_ip=_IP,
        idrac_username=os.environ.get("HPE_EMULATOR_USER", "root"),
        idrac_password=os.environ.get("HPE_EMULATOR_PASSWORD", "root_password"),
        idrac_port=int(os.environ.get("HPE_EMULATOR_PORT", "45678")),
        insecure=True,
        is_debug=False,
    )


def test_hpe_emulator_discovery():
    """Discovery resolves a real iLO system id over the wire."""
    mgr = _manager()
    systems = mgr.discover_computer_system_ids()
    assert systems, "no ComputerSystem discovered from the live emulator"
    assert all(s.startswith("/redfish/v1/Systems/") for s in systems)


def test_hpe_emulator_read_command():
    """A vendor-neutral read command returns data from the live emulator."""
    mgr = _manager()
    result = mgr.sync_invoke(ApiRequestType.Sensors, "sensors")
    assert isinstance(result.data, list)
