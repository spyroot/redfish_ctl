"""Dual-mode-style coverage for DellLCService export actions."""

import json
from pathlib import Path

import pytest
from vendor_corpus import corpus_dir

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.dell_lc.cmd_dell_lc_export import DellLcExport
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_manager_shared import ApiRequestType

DELL_CORPUS = corpus_dir(
    Path(__file__).parent / "dell_xr8620t_corpus.tar.gz", "10.252.252.209"
)
DELL_INDEX = {path.name.lower(): path for path in DELL_CORPUS.glob("*.json")}
LC_SERVICE = "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellLCService"
LC_ACTIONS = f"{LC_SERVICE}/Actions/DellLCService"


def _fixture_for_path(path):
    """Return the extracted Dell fixture matching a Redfish path.

    :param path: request path from requests-mock.
    :return: fixture path, or None when the corpus lacks the resource.
    """
    name = "_" + path.strip("/").replace("/", "_") + ".json"
    return DELL_INDEX.get(name.lower())


@pytest.fixture
def dell_lc_export_manager():
    """Serve the committed Dell corpus over requests-mock.

    :return: tuple of RedfishManagerBase and recorded requests list.
    """
    requests_mock = pytest.importorskip("requests_mock")
    requests = []

    def get_cb(request, context):
        requests.append(request)
        fixture = _fixture_for_path(request.path)
        if fixture is None:
            context.status_code = 404
            return json.dumps({"error": f"no fixture for {request.path}"})
        context.status_code = 200
        return fixture.read_text()

    def post_cb(request, context):
        requests.append(request)
        context.status_code = 202
        context.headers["Location"] = "/redfish/v1/TaskService/Tasks/lc-export-1"
        return json.dumps({
            "Task": {"@odata.id": "/redfish/v1/TaskService/Tasks/lc-export-1"}
        })

    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=get_cb)
        mocker.post(requests_mock.ANY, text=post_cb)
        manager = RedfishManagerBase(
            idrac_ip="mock-dell-lc-export",
            idrac_username="root",
            idrac_password="mock",
            insecure=True,
            is_debug=False,
        )
        yield manager, requests


def _post_requests(requests):
    """Return POST requests recorded by the mock Redfish transport.

    :param requests: recorded requests-mock request objects.
    :return: list of POST requests.
    """
    return [request for request in requests if request.method == "POST"]


def _export_rows(result):
    """Return export metadata rows keyed by export choice.

    :param result: dell-lc-export metadata CommandResult.
    :return: dict of export choice to metadata row.
    """
    return {item["export"]: item for item in result.data["export_actions"]}


def test_dell_lc_export_lists_corpus_targets_without_mutating(
    dell_lc_export_manager,
):
    """No export choice lists corpus-advertised LC export actions and never POSTs."""
    manager, requests = dell_lc_export_manager

    result = manager.sync_invoke(ApiRequestType.DellLcExport, "dell-lc-export")

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["lc_service"] == LC_SERVICE
    rows = _export_rows(result)
    assert rows["lc-log"]["target"] == f"{LC_ACTIONS}.ExportLCLog"
    assert rows["hw-inventory"]["allowed"]["ShareType"] == [
        "CIFS",
        "HTTP",
        "HTTPS",
        "Local",
        "NFS",
    ]
    assert rows["tech-support-report"]["allowed"]["DataSelectorArrayIn"] == [
        "HWData",
        "OSAppData",
        "OSAppDataWithoutPII",
        "TTYLogs",
    ]
    assert _post_requests(requests) == []


def test_dell_lc_export_without_confirm_previews_payload_only(
    dell_lc_export_manager,
):
    """DellLCService.ExportLCLog resolves the target but does not POST by default."""
    manager, requests = dell_lc_export_manager

    result = manager.sync_invoke(
        ApiRequestType.DellLcExport,
        "dell-lc-export",
        export_name="lc-log",
        share_type="NFS",
        ip_address="192.0.2.10",
        share_name="/exports/lc",
        file_name="lc.log",
        ignore_cert_warning="On",
        proxy_support="Off",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == "#DellLCService.ExportLCLog"
    assert result.data["target"] == f"{LC_ACTIONS}.ExportLCLog"
    assert result.data["level"] == "destructive"
    assert result.data["blocked"] == "DellLCService export requires --confirm"
    assert result.data["payload"] == {
        "ShareType": "NFS",
        "IPAddress": "192.0.2.10",
        "ShareName": "/exports/lc",
        "FileName": "lc.log",
        "IgnoreCertWarning": "On",
        "ProxySupport": "Off",
    }
    assert _post_requests(requests) == []


def test_dell_lc_export_confirm_posts_hw_inventory_payload(
    dell_lc_export_manager,
):
    """--confirm POSTs the selected export payload to the discovered target."""
    manager, requests = dell_lc_export_manager

    result = manager.sync_invoke(
        ApiRequestType.DellLcExport,
        "dell-lc-export",
        export_name="hw-inventory",
        share_type="HTTPS",
        ip_address="192.0.2.20",
        share_name="reports",
        file_name="hw.json",
        xml_schema="JSON",
        confirm=True,
    )

    posts = _post_requests(requests)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#DellLCService.ExportHWInventory"
    assert result.data["target"] == f"{LC_ACTIONS}.ExportHWInventory"
    assert result.data["task_id"] == "lc-export-1"
    assert len(posts) == 1
    assert posts[0].path.lower() == f"{LC_ACTIONS}.ExportHWInventory".lower()
    assert posts[0].json() == {
        "ShareType": "HTTPS",
        "IPAddress": "192.0.2.20",
        "ShareName": "reports",
        "FileName": "hw.json",
        "XMLSchema": "JSON",
    }


def test_dell_lc_export_dry_run_overrides_confirm(dell_lc_export_manager):
    """--dry_run remains a no-POST preview even when --confirm is also supplied."""
    manager, requests = dell_lc_export_manager

    result = manager.sync_invoke(
        ApiRequestType.DellLcExport,
        "dell-lc-export",
        export_name="lc-log",
        share_type="Local",
        confirm=True,
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] is None
    assert result.data["payload"] == {"ShareType": "Local"}
    assert _post_requests(requests) == []


def test_dell_lc_export_rejects_invalid_share_type(dell_lc_export_manager):
    """Inline allowable values reject an unsupported ShareType before POST."""
    manager, requests = dell_lc_export_manager

    result = manager.sync_invoke(
        ApiRequestType.DellLcExport,
        "dell-lc-export",
        export_name="lc-log",
        share_type="FTP",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "invalid value for DellLCService.ExportLCLog ShareType: FTP; "
        "allowed: CIFS, HTTP, HTTPS, Local, NFS"
    )
    assert result.data["validation_errors"] == [
        {
            "parameter": "ShareType",
            "value": "FTP",
            "allowed": ["CIFS", "HTTP", "HTTPS", "Local", "NFS"],
        }
    ]
    assert _post_requests(requests) == []


def test_dell_lc_export_rejects_invalid_data_selector(dell_lc_export_manager):
    """Inline allowable values reject unsupported support-report selectors."""
    manager, requests = dell_lc_export_manager

    result = manager.sync_invoke(
        ApiRequestType.DellLcExport,
        "dell-lc-export",
        export_name="tech-support-report",
        data_selectors=["HWData", "DebugLogs"],
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "invalid value for DellLCService.ExportTechSupportReport "
        "DataSelectorArrayIn: DebugLogs; allowed: HWData, OSAppData, "
        "OSAppDataWithoutPII, TTYLogs"
    )
    assert _post_requests(requests) == []


def test_dell_lc_export_redacts_password_from_env(
    dell_lc_export_manager,
    monkeypatch,
):
    """Dry-run output does not echo a share password read from env."""
    manager, requests = dell_lc_export_manager
    monkeypatch.setenv("LC_EXPORT_PASSWORD", "placeholder-value")

    result = manager.sync_invoke(
        ApiRequestType.DellLcExport,
        "dell-lc-export",
        export_name="lc-log",
        share_type="CIFS",
        share_username="share-user",
        share_password_env="LC_EXPORT_PASSWORD",
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["payload"]["UserName"] == "share-user"
    assert result.data["payload"]["Password"] == "********"
    assert _post_requests(requests) == []


def test_dell_lc_export_rejects_missing_password_env(dell_lc_export_manager):
    """Missing password environment variables fail before any POST."""
    manager, requests = dell_lc_export_manager

    with pytest.raises(
        InvalidArgument,
        match="environment variable 'MISSING_LC_EXPORT_PASSWORD'",
    ):
        manager.sync_invoke(
            ApiRequestType.DellLcExport,
            "dell-lc-export",
            export_name="lc-log",
            share_password_env="MISSING_LC_EXPORT_PASSWORD",
            confirm=True,
        )

    assert _post_requests(requests) == []


def test_dell_lc_export_legacy_fixture_reports_missing_export(redfish_api):
    """The legacy small fixture is discovered but reports no LC export actions."""
    result = redfish_api.sync_invoke(
        ApiRequestType.DellLcExport,
        "dell-lc-export",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == {
        "lc_service": "/redfish/v1/Dell/Managers/iDRAC.Embedded.1/DellLCService",
        "export_actions": [],
    }


def test_dell_lc_export_registers_cli_help():
    """The command registry exposes dell-lc-export and its safety flags."""
    registry = RedfishManagerBase().get_registry()
    assert registry[ApiRequestType.DellLcExport]["dell-lc-export"] is DellLcExport

    parser, command_name, help_text = DellLcExport.register_subcommand(DellLcExport)

    assert command_name == "dell-lc-export"
    assert "export Dell Lifecycle Controller" in help_text
    cmd_help = parser.format_help()
    assert "--export" in cmd_help
    assert "--confirm" in cmd_help
    assert "--dry_run" in cmd_help
