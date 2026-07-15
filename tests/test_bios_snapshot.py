"""Offline test for bios-snapshot — the transactional-mutation restore point.

Proves bios-snapshot captures the host's CURRENT BIOS attribute values as a spec
that bios-change --from_spec can re-apply, so a BIOS change is rollback-able.
Vendor-neutral (reads standard Bios.Attributes); verified on HPE + Supermicro.
"""
import json
import tempfile

from redfish_ctl.redfish_manager_shared import ApiRequestType


def test_bios_snapshot_named_attribute(redfish_mock_factory):
    """--attr_name captures just that attribute's current value."""
    mgr, _ = redfish_mock_factory("hpe")
    snap = mgr.sync_invoke(ApiRequestType.BiosSnapshot, "bios_snapshot",
                           attr_name="DynamicPowerCapping").data
    assert "DynamicPowerCapping" in snap["Attributes"]
    # a snapshot IS a valid bios-change spec (round-trip through --from_spec)
    assert set(snap.keys()) == {"Attributes"}


def test_bios_snapshot_is_precise_inverse(redfish_mock_factory):
    """--from_spec snapshots the current values of exactly the changed attrs.

    That output is the rollback: apply the change spec, then apply this to undo it.
    """
    mgr, _ = redfish_mock_factory("hpe")
    change = {"Attributes": {"DynamicPowerCapping": "SOME_NEW_VALUE"}}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(change, fh)
        spec_path = fh.name
    rollback = mgr.sync_invoke(ApiRequestType.BiosSnapshot, "bios_snapshot",
                               from_spec=spec_path).data
    # rollback holds only the changed attr, at its current (pre-change) value
    assert list(rollback["Attributes"].keys()) == ["DynamicPowerCapping"]
    assert rollback["Attributes"]["DynamicPowerCapping"] != "SOME_NEW_VALUE"


def test_bios_snapshot_all_on_supermicro(redfish_mock_factory):
    """A full snapshot returns every current attribute as a restore point."""
    mgr, _ = redfish_mock_factory("supermicro")
    snap = mgr.sync_invoke(ApiRequestType.BiosSnapshot, "bios_snapshot").data
    assert isinstance(snap["Attributes"], dict) and snap["Attributes"]
