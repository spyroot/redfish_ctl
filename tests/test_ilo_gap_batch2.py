"""Offline tests for the second iLO gap batch: secure-boot, firmware-update, triggers.

Proves three more standard-Redfish capabilities the coverage audit flagged as
missing, verified against the HPE iLO and GB300 corpora. firmware-update is a
guarded mutating action — the tests assert it never POSTs without --confirm.
"""
from redfish_ctl.idrac_shared import ApiRequestType


def _post_count(svc):
    return sum(1 for r in svc.requests if r.method == "POST")


def test_secure_boot_reads_state_and_databases_ilo(redfish_mock_factory):
    """secure-boot returns SecureBoot state + one row per key database on iLO."""
    mgr, _ = redfish_mock_factory("hpe")
    result = mgr.sync_invoke(ApiRequestType.SecureBoot, "secure-boot")
    assert isinstance(result.data, list) and result.data
    row = result.data[0]
    assert "SecureBootEnable" in row and "SecureBootMode" in row
    # PK/KEK/db/dbx databases surface with their ids
    assert any(r["DatabaseId"] for r in result.data)


def test_secure_boot_on_supermicro(redfish_mock_factory):
    """secure-boot also resolves on the GB300 tree (vendor-neutral)."""
    mgr, _ = redfish_mock_factory("supermicro")
    result = mgr.sync_invoke(ApiRequestType.SecureBoot, "secure-boot")
    assert isinstance(result.data, list) and result.data


def test_telemetry_triggers_ilo(redfish_mock_factory):
    """telemetry-triggers lists iLO metric alert thresholds."""
    mgr, _ = redfish_mock_factory("hpe")
    result = mgr.sync_invoke(ApiRequestType.Triggers, "telemetry-triggers")
    assert isinstance(result.data, list) and result.data
    assert all("MetricType" in r for r in result.data)


def test_firmware_update_dry_run_guarded_ilo(redfish_mock_factory):
    """firmware-update resolves SimpleUpdate and dry-runs without --confirm."""
    mgr, svc = redfish_mock_factory("hpe")
    result = mgr.sync_invoke(ApiRequestType.FirmwareUpdate, "firmware-update",
                             image_uri="http://example/fw.bin")
    assert result.data["dry_run"] is True
    assert result.data["level"] == "destructive"
    assert result.data["target"].endswith("/UpdateService.SimpleUpdate")
    assert _post_count(svc) == 0, "firmware flash must not POST without --confirm"


def test_firmware_update_fires_with_confirm_ilo(redfish_mock_factory):
    """firmware-update --confirm POSTs the image payload to SimpleUpdate."""
    mgr, svc = redfish_mock_factory("hpe")
    result = mgr.sync_invoke(ApiRequestType.FirmwareUpdate, "firmware-update",
                             image_uri="http://example/fw.bin", confirm=True)
    assert result.data.get("executed") is True
    assert _post_count(svc) == 1
    assert svc.last_request.json().get("ImageURI") == "http://example/fw.bin"


def test_gap_batch2_graceful_on_dell(redfish_mock):
    """All three degrade gracefully on the Dell mock (no crash, no stray POST)."""
    sb = redfish_mock.sync_invoke(ApiRequestType.SecureBoot, "secure-boot")
    tr = redfish_mock.sync_invoke(ApiRequestType.Triggers, "telemetry-triggers")
    fw = redfish_mock.sync_invoke(ApiRequestType.FirmwareUpdate, "firmware-update",
                                  image_uri="http://example/fw.bin")
    assert isinstance(sb.data, list)
    assert isinstance(tr.data, list)
    # firmware-update without a resolvable SimpleUpdate must not have fired
    assert not (isinstance(fw.data, dict) and fw.data.get("executed"))
