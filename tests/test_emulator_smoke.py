"""Opt-in emulator-backed smoke test (the "live-like" lane).

This validates redfish_ctl's real Redfish client against a spec-conformant
emulator running locally, with no hardware. The recommended emulator is the
OpenStack sushy-tools fake driver (pip install sushy-tools), which needs no
libvirt and supports mutating Actions:

    sushy-emulator --fake -i 127.0.0.1 -p 8000

Then point this test at it:

    REDFISH_EMULATOR_URL=http://127.0.0.1:8000 pytest tests/test_emulator_smoke.py

It is skipped by default so the offline suite stays hermetic. The emulator
serves *generic* Redfish (not Dell OEM paths), so it validates the transport
and generic capabilities, complementing the Dell-shaped mock fixtures.

Author Mus spyroot@gmail.com
"""
import json
import os

import pytest

_EMU = os.environ.get("REDFISH_EMULATOR_URL", "").rstrip("/")
_skip = pytest.mark.skipif(
    not _EMU,
    reason="set REDFISH_EMULATOR_URL (e.g. run `sushy-emulator --fake`) to enable",
)


def _client():
    from redfish_ctl.base_manager import CommandBase
    # host:port parsed from the emulator URL; api_*_call takes full URLs anyway.
    host = _EMU.split("://", 1)[-1]
    return CommandBase(
        idrac_ip=host,
        idrac_username=os.environ.get("REDFISH_EMULATOR_USER", "admin"),
        idrac_password=os.environ.get("REDFISH_EMULATOR_PASSWORD", "password"),
        insecure=True,
        is_debug=False,
    )


@_skip
def test_emulator_systems_get():
    """The client can read the Systems collection from a live emulator."""
    api = _client()
    resp = api.api_get_call(f"{_EMU}/redfish/v1/Systems", {})
    assert resp.status_code == 200
    assert resp.json()["Members"], "emulator exposed no systems"


@_skip
def test_emulator_reset_action():
    """A mutating ComputerSystem.Reset succeeds against the emulator."""
    api = _client()
    members = api.api_get_call(f"{_EMU}/redfish/v1/Systems", {}).json()["Members"]
    sys_id = members[0]["@odata.id"]
    resp = api.api_post_call(
        f"{_EMU}{sys_id}/Actions/ComputerSystem.Reset",
        json.dumps({"ResetType": "On"}),
        {},
    )
    assert resp.status_code in (200, 202, 204)
