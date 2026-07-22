"""Dual-mode tests for the credential-less BMC segment scan command."""
import json

from redfish_ctl.discovery import cmd_bmc_scan
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def test_bmc_scan_uses_mocked_segment_scan_without_redfish_requests(
    redfish_mock,
    redfish_service,
    monkeypatch,
):
    """bmc-scan delegates CIDR probing and does not query the fixture BMC."""
    found = [
        {
            "IP": "192.0.2.10",
            "Vendor": "dell",
            "Product": "PowerEdge",
            "RedfishVersion": "1.15.0",
            "Auth": "open",
        },
        {
            "IP": "192.0.2.11",
            "Vendor": "unknown",
            "Product": None,
            "RedfishVersion": None,
            "Auth": "required",
        },
    ]
    calls = []

    def fake_scan_segment(subnet, port, timeout, workers):
        calls.append((subnet, port, timeout, workers))
        return found

    monkeypatch.setattr(cmd_bmc_scan, "scan_segment", fake_scan_segment)

    result = redfish_mock.sync_invoke(
        ApiRequestType.BmcScan,
        "bmc-scan",
        subnet="192.0.2.0/30",
        scan_port=8443,
        scan_timeout=0.25,
        scan_workers=4,
    )

    assert isinstance(result, CommandResult)
    assert result.data == found
    assert result.discovered is None
    assert result.extra is None
    assert result.error is None
    json.dumps(result.data)
    assert calls == [("192.0.2.0/30", 8443, 0.25, 4)]
    assert redfish_service.requests == []


def test_bmc_scan_invalid_subnet_reports_error_without_redfish_requests(
    redfish_mock,
    redfish_service,
    monkeypatch,
):
    """An invalid CIDR is reported as a command error, not a network request."""

    def fake_scan_segment(subnet, port, timeout, workers):
        raise ValueError(f"{subnet} is not a network")

    monkeypatch.setattr(cmd_bmc_scan, "scan_segment", fake_scan_segment)

    result = redfish_mock.sync_invoke(
        ApiRequestType.BmcScan,
        "bmc-scan",
        subnet="not-a-cidr",
    )

    assert isinstance(result, CommandResult)
    assert result.data == []
    assert result.discovered is None
    assert result.extra is None
    assert "invalid subnet 'not-a-cidr'" in result.error
    assert redfish_service.requests == []


def test_bmc_scan_saves_found_rows_when_filename_is_provided(
    redfish_mock,
    monkeypatch,
    tmp_path,
):
    """bmc-scan writes the discovered rows through the common save helper."""
    found = [
        {
            "IP": "192.0.2.20",
            "Vendor": "supermicro",
            "Product": "GB300",
            "RedfishVersion": "1.16.0",
            "Auth": "open",
        }
    ]
    monkeypatch.setattr(cmd_bmc_scan, "scan_segment", lambda *_: found)
    output = tmp_path / "bmc-scan.json"

    result = redfish_mock.sync_invoke(
        ApiRequestType.BmcScan,
        "bmc-scan",
        subnet="192.0.2.20/32",
        filename=str(output),
    )

    assert isinstance(result, CommandResult)
    assert result.data == found
    assert json.loads(output.read_text()) == found
