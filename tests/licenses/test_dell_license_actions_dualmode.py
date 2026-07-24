"""Dual-mode-style coverage for Dell OEM license-management actions."""

import json
from pathlib import Path

import pytest
from vendor_corpus import corpus_dir

from redfish_ctl.actions.action_policy import Destructiveness, classify
from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.licenses.cmd_dell_license_actions import DellLicenseActions
from redfish_ctl.redfish_manager import CommandResult

DELL_CORPUS = corpus_dir(
    Path(__file__).parent.parent / "dell_xr8620t_corpus.tar.gz", "10.252.252.209"
)
DELL_INDEX = {path.name.lower(): path for path in DELL_CORPUS.glob("*.json")}
SERVICE_URI = (
    "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/"
    "DellLicenseManagementService"
)
DELETE_ACTION = "#DellLicenseManagementService.DeleteLicense"
DELETE_TARGET = f"{SERVICE_URI}/Actions/DellLicenseManagementService.DeleteLicense"
EXPORT_SHARE_ACTION = "#DellLicenseManagementService.ExportLicenseToNetworkShare"
EXPORT_SHARE_TARGET = (
    f"{SERVICE_URI}/Actions/DellLicenseManagementService.ExportLicenseToNetworkShare"
)
IMPORT_ACTION = "#DellLicenseManagementService.ImportLicense"
IMPORT_TARGET = f"{SERVICE_URI}/Actions/DellLicenseManagementService.ImportLicense"


def _fixture_for_path(path):
    """Return the extracted Dell fixture matching a Redfish path.

    :param path: request path from requests-mock.
    :return: fixture path, or None when the corpus lacks the resource.
    """
    name = "_" + path.strip("/").replace("/", "_") + ".json"
    return DELL_INDEX.get(name.lower())


@pytest.fixture
def dell_license_action_manager():
    """Serve the committed Dell corpus over requests-mock.

    :return: tuple of IDracManager and recorded requests list.
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
        context.headers["Location"] = "/redfish/v1/TaskService/Tasks/license-action-1"
        return json.dumps({
            "Task": {"@odata.id": "/redfish/v1/TaskService/Tasks/license-action-1"}
        })

    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=get_cb)
        mocker.post(requests_mock.ANY, text=post_cb)
        manager = IDracManager(
            idrac_ip="mock-dell-license-actions",
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


def test_dell_license_actions_lists_targets_without_mutating(
    dell_license_action_manager,
):
    """With no selected action, the command lists targets and never POSTs."""
    manager, requests = dell_license_action_manager

    result = manager.sync_invoke(
        ApiRequestType.DellLicenseActions,
        "dell-license-actions",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["license_management_service"] == SERVICE_URI
    assert result.data["selectors"]["delete"] == {
        "action": DELETE_ACTION,
        "target": DELETE_TARGET,
        "available": True,
        "level": "destructive",
    }
    assert result.data["selectors"]["export-to-share"] == {
        "action": EXPORT_SHARE_ACTION,
        "target": EXPORT_SHARE_TARGET,
        "available": True,
        "level": "destructive",
    }
    supported = {
        row["selector"]
        for row in result.data["actions"]
        if row["supported"]
    }
    assert {
        "delete",
        "export",
        "export-by-device",
        "export-by-device-to-share",
        "export-to-share",
        "import",
        "import-from-share",
    } <= supported
    assert _post_requests(requests) == []


def test_dell_license_delete_previews_without_confirm(
    dell_license_action_manager,
):
    """DeleteLicense resolves the Dell target but does not POST by default."""
    manager, requests = dell_license_action_manager

    result = manager.sync_invoke(
        ApiRequestType.DellLicenseActions,
        "dell-license-actions",
        action="delete",
        entitlement_id="49195PA",
        delete_option="Force",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == DELETE_ACTION
    assert result.data["target"] == DELETE_TARGET
    assert result.data["level"] == "destructive"
    assert result.data["blocked"] == "destructive action requires --confirm"
    assert result.data["payload"] == {
        "EntitlementID": "49195PA",
        "DeleteOptions": "Force",
    }
    assert _post_requests(requests) == []


def test_dell_license_export_to_share_confirm_posts_payload(
    dell_license_action_manager,
    monkeypatch,
):
    """--confirm POSTs the network-share export payload to the Dell target."""
    manager, requests = dell_license_action_manager
    monkeypatch.setenv("LICENSE_SHARE_PASSWORD", "placeholder-value")

    result = manager.sync_invoke(
        ApiRequestType.DellLicenseActions,
        "dell-license-actions",
        action="export-to-share",
        share_type="CIFS",
        share_address="192.0.2.10",
        share_name="licenses",
        file_name="idrac-license.xml",
        share_username="license-writer",
        share_password_env="LICENSE_SHARE_PASSWORD",
        confirm=True,
    )

    posts = _post_requests(requests)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == EXPORT_SHARE_ACTION
    assert result.data["target"] == EXPORT_SHARE_TARGET
    assert result.data["level"] == "destructive"
    assert result.data["task_id"] == "license-action-1"
    assert len(posts) == 1
    assert posts[0].path.lower() == EXPORT_SHARE_TARGET.lower()
    assert posts[0].json() == {
        "ShareType": "CIFS",
        "IPAddress": "192.0.2.10",
        "ShareName": "licenses",
        "FileName": "idrac-license.xml",
        "UserName": "license-writer",
        "Password": "placeholder-value",
    }


def test_dell_license_share_password_is_redacted_in_preview(
    dell_license_action_manager,
    monkeypatch,
):
    """Dry-run output does not echo share or proxy passwords."""
    manager, requests = dell_license_action_manager
    monkeypatch.setenv("LICENSE_SHARE_PASSWORD", "placeholder-value")
    monkeypatch.setenv("LICENSE_PROXY_PASSWORD", "proxy-placeholder")

    result = manager.sync_invoke(
        ApiRequestType.DellLicenseActions,
        "dell-license-actions",
        action="import-from-share",
        share_type="HTTPS",
        share_address="repo.example.test",
        file_name="idrac-license.xml",
        share_password_env="LICENSE_SHARE_PASSWORD",
        proxy_support="ParametersProxy",
        proxy_type="HTTP",
        proxy_server="proxy.example.test",
        proxy_port=8080,
        proxy_username="proxy-user",
        proxy_password_env="LICENSE_PROXY_PASSWORD",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["payload"]["Password"] == "********"
    assert result.data["payload"]["ProxyPassword"] == "********"
    assert result.data["payload"]["ProxyPort"] == 8080
    assert _post_requests(requests) == []


def test_dell_license_export_rejects_invalid_share_type(
    dell_license_action_manager,
):
    """Inline allowable values reject an unsupported ShareType before POST."""
    manager, requests = dell_license_action_manager

    result = manager.sync_invoke(
        ApiRequestType.DellLicenseActions,
        "dell-license-actions",
        action="export-to-share",
        share_type="FTP",
        share_address="192.0.2.10",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "invalid value for DellLicenseManagementService.ExportLicenseToNetworkShare "
        "ShareType: FTP; allowed: CIFS, HTTP, HTTPS, NFS"
    )
    assert result.data["validation_errors"] == [
        {
            "parameter": "ShareType",
            "value": "FTP",
            "allowed": ["CIFS", "HTTP", "HTTPS", "NFS"],
        }
    ]
    assert _post_requests(requests) == []


def test_dell_license_by_device_requires_device(
    dell_license_action_manager,
):
    """By-device export selectors fail closed without a device identifier."""
    manager, requests = dell_license_action_manager

    with pytest.raises(InvalidArgument, match="requires --device"):
        manager.sync_invoke(
            ApiRequestType.DellLicenseActions,
            "dell-license-actions",
            action="export-by-device",
            confirm=True,
        )

    assert _post_requests(requests) == []


def test_dell_license_direct_import_reads_file_and_redacts(
    dell_license_action_manager,
    tmp_path,
):
    """ImportLicense can read local license content without echoing it."""
    manager, requests = dell_license_action_manager
    license_file = tmp_path / "license.xml"
    license_file.write_text("<License>placeholder</License>\n", encoding="utf-8")

    result = manager.sync_invoke(
        ApiRequestType.DellLicenseActions,
        "dell-license-actions",
        action="import",
        import_option="Force",
        license_data_file=str(license_file),
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["action"] == IMPORT_ACTION
    assert result.data["target"] == IMPORT_TARGET
    assert result.data["payload"] == {
        "ImportOptions": "Force",
        "LicenseFile": "********",
    }
    assert _post_requests(requests) == []


def test_dell_license_missing_action_reports_available(
    dell_license_action_manager,
    monkeypatch,
):
    """A service without DeleteLicense reports available actions and does not POST."""
    manager, requests = dell_license_action_manager

    def service_without_delete(self, do_async):
        fixture = _fixture_for_path(SERVICE_URI)
        data = json.loads(fixture.read_text())
        data["Actions"].pop(DELETE_ACTION)
        return SERVICE_URI, data

    monkeypatch.setattr(
        DellLicenseActions,
        "_license_management_service",
        service_without_delete,
    )

    result = manager.sync_invoke(
        ApiRequestType.DellLicenseActions,
        "dell-license-actions",
        action="delete",
        entitlement_id="49195PA",
    )

    assert isinstance(result, CommandResult)
    assert result.error == f"action '{DELETE_ACTION}' not found on {SERVICE_URI}"
    assert DELETE_ACTION not in result.data["available"]
    assert EXPORT_SHARE_ACTION in result.data["available"]
    assert _post_requests(requests) == []


def test_dell_license_actions_policy_and_registry():
    """The Dell license actions are guarded and the command self-registers."""
    registry = IDracManager.get_registry()

    assert classify(DELETE_ACTION) is Destructiveness.DESTRUCTIVE
    assert classify(EXPORT_SHARE_ACTION) is Destructiveness.DESTRUCTIVE
    assert "dell-license-actions" in registry[ApiRequestType.DellLicenseActions]

    cmd_parser, cmd_name, cmd_help = registry[ApiRequestType.DellLicenseActions][
        "dell-license-actions"
    ].register_subcommand(
        registry[ApiRequestType.DellLicenseActions]["dell-license-actions"]
    )
    assert cmd_name == "dell-license-actions"
    assert "license actions" in cmd_help
    parsed = cmd_parser.parse_args([
        "--action",
        "delete",
        "--entitlement-id",
        "49195PA",
        "--confirm",
    ])
    assert parsed.action == "delete"
    assert parsed.entitlement_id == "49195PA"
    assert parsed.confirm is True
