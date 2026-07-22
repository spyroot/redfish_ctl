"""Offline test: bios-change is vendor-neutral (the Intel/AMD tuning vehicle).

bios-change previously read the Dell BiosRegistry and PATCHed a hardcoded Dell
path. It now resolves the attribute registry via /redfish/v1/Registries/ and the
Settings object via Bios.@Redfish.Settings, so BIOS tuning works off Dell. The
Dell Jobs commit stays behind --commit/--commit_pending. Verified on the HPE iLO
corpus (Intel tuning attributes) with Dell behavior preserved.
"""
from redfish_ctl.idrac_shared import ApiRequestType


def test_bios_change_builds_payload_on_ilo(redfish_mock_factory):
    """--do_show resolves the iLO registry and builds a pending BIOS payload."""
    mgr, _ = redfish_mock_factory("hpe")
    result = mgr.sync_invoke(ApiRequestType.BiosChangeSettings, "bios_change_settings",
                             attr_name="DynamicPowerCapping", attr_value="Enabled",
                             do_show=True)
    attrs = result.data.get("Attributes", {})
    assert attrs.get("DynamicPowerCapping") == "Enabled"
    # applied as a pending change on reset (standard Redfish, no Dell Jobs queue)
    assert result.data.get("@Redfish.SettingsApplyTime", {}).get("ApplyTime") == "OnReset"


def test_bios_change_patches_settings_on_ilo(redfish_mock_factory):
    """The default (no --commit) path PATCHes the resolved Settings object on iLO."""
    mgr, svc = redfish_mock_factory("hpe")
    result = mgr.sync_invoke(ApiRequestType.BiosChangeSettings, "bios_change_settings",
                             attr_name="DynamicPowerCapping", attr_value="Enabled")
    assert result is not None
    # a PATCH was issued to a BIOS settings path (no crash, no Dell Jobs commit)
    assert any(r.method == "PATCH" and "bios" in r.path.lower() for r in svc.requests)


def test_bios_change_still_works_on_dell(redfish_mock):
    """Dell path unchanged: --do_show builds the same pending payload."""
    result = redfish_mock.sync_invoke(ApiRequestType.BiosChangeSettings, "bios_change_settings",
                                      attr_name="DynamicPowerCapping", attr_value="Enabled",
                                      do_show=True)
    assert result.data.get("Attributes", {}).get("DynamicPowerCapping") == "Enabled"
