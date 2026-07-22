"""Dual-mode tests for Dell SupportAssist export actions."""
import copy
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.actions.action_policy import Destructiveness, classify
from redfish_ctl.dell_lc.cmd_dell_lc_supportassist_export import (
    DellLcSupportAssistExport,
)
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

REPO_ROOT = Path(__file__).resolve().parents[1]
DELL_CORPUS = corpus_dir(
    REPO_ROOT / "tests" / "dell_xr8620t_corpus.tar.gz",
    "10.252.252.209",
)
LC_SERVICE = (
    "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellLCService"
)
EXPORT_TARGET = (
    f"{LC_SERVICE}/Actions/"
    "DellLCService.SupportAssistExportLastCollection"
)


@pytest.fixture
def dell_lc_mock():
    """Return a manager and mock service backed by the Dell XR8620t corpus.

    :return: tuple of Redfish manager and mock service.
    """
    requests_mock = pytest.importorskip("requests_mock")
    service = MockRedfishService(
        DELL_CORPUS,
        index=_build_fixture_index(DELL_CORPUS),
    )
    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=service.get_cb)
        mocker.patch(requests_mock.ANY, text=service.patch_cb)
        mocker.post(requests_mock.ANY, text=service.post_cb)
        mocker.delete(requests_mock.ANY, text=service.delete_cb)
        service.mocker = mocker
        yield (
            IDracManager(
                idrac_ip="mock-dell-lc",
                idrac_username="root",
                idrac_password="mock",
                insecure=True,
                is_debug=False,
            ),
            service,
        )


def _post_requests(service):
    """Return POST requests recorded by the mock Redfish service.

    :param service: mock Redfish service.
    :return: recorded POST requests.
    """
    return [request for request in service.requests if request.method == "POST"]


def _overlay_lc_service(service, body):
    """Overlay DellLCService under both common request casings."""
    service._overlay[LC_SERVICE] = body
    service._overlay[LC_SERVICE.lower()] = body


def test_supportassist_export_lists_corpus_target_without_post(dell_lc_mock):
    """Listing discovers SupportAssistExportLastCollection and never POSTs."""
    manager, service = dell_lc_mock

    result = manager.sync_invoke(
        ApiRequestType.DellLcSupportAssistExport,
        "dell-lc-supportassist-export",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["lc_service"] == LC_SERVICE
    assert result.data["action"] == (
        "#DellLCService.SupportAssistExportLastCollection"
    )
    assert result.data["target"] == EXPORT_TARGET
    assert result.data["allowed"]["ShareType"] == [
        "CIFS",
        "FTP",
        "HTTP",
        "HTTPS",
        "NFS",
        "TFTP",
    ]
    assert result.data["allowed"]["ProxySupport"] == [
        "DefaultProxy",
        "Off",
        "ParametersProxy",
    ]
    assert _post_requests(service) == []


def test_supportassist_export_dry_run_redacts_share_password(
    dell_lc_mock,
    monkeypatch,
):
    """Preview output masks the share password and sends no POST."""
    manager, service = dell_lc_mock
    monkeypatch.setenv("DELL_SUPPORTASSIST_PASSWORD", "placeholder-password")

    result = manager.sync_invoke(
        ApiRequestType.DellLcSupportAssistExport,
        "dell-lc-supportassist-export",
        share_type="HTTPS",
        ip_address="repo.example.test",
        share_name="/support",
        file_name="last-collection.zip",
        share_username="report-user",
        share_password_env="DELL_SUPPORTASSIST_PASSWORD",
        ignore_cert_warning="On",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["level"] == "destructive"
    assert result.data["blocked"] == "destructive action requires --confirm"
    assert result.data["action"] == (
        "#DellLCService.SupportAssistExportLastCollection"
    )
    assert result.data["target"] == EXPORT_TARGET
    assert result.data["payload"] == {
        "ShareType": "HTTPS",
        "IPAddress": "repo.example.test",
        "ShareName": "/support",
        "FileName": "last-collection.zip",
        "UserName": "report-user",
        "Password": "********",
        "IgnoreCertWarning": "On",
    }
    assert _post_requests(service) == []


def test_supportassist_export_confirm_posts_payload(dell_lc_mock):
    """--confirm POSTs one export request to the discovered target."""
    manager, service = dell_lc_mock

    result = manager.sync_invoke(
        ApiRequestType.DellLcSupportAssistExport,
        "dell-lc-supportassist-export",
        share_type="NFS",
        ip_address="192.0.2.10",
        share_name="/exports",
        file_name="last-collection.zip",
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["level"] == "destructive"
    assert len(posts) == 1
    assert posts[0].path.lower() == EXPORT_TARGET.lower()
    assert posts[0].json() == {
        "ShareType": "NFS",
        "IPAddress": "192.0.2.10",
        "ShareName": "/exports",
        "FileName": "last-collection.zip",
    }


def test_supportassist_export_rejects_invalid_share_type(dell_lc_mock):
    """Inline Dell allowable values reject unsupported ShareType input."""
    manager, service = dell_lc_mock

    result = manager.sync_invoke(
        ApiRequestType.DellLcSupportAssistExport,
        "dell-lc-supportassist-export",
        share_type="SMTP",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "invalid value for DellLCService.SupportAssistExportLastCollection "
        "ShareType: SMTP; allowed: CIFS, FTP, HTTP, HTTPS, NFS, TFTP"
    )
    assert result.data["validation_errors"] == [
        {
            "parameter": "ShareType",
            "value": "SMTP",
            "allowed": ["CIFS", "FTP", "HTTP", "HTTPS", "NFS", "TFTP"],
        }
    ]
    assert _post_requests(service) == []


def test_supportassist_export_reports_missing_action_without_post(dell_lc_mock):
    """A service missing SupportAssistExportLastCollection reports the miss."""
    manager, service = dell_lc_mock
    body = copy.deepcopy(service._state(LC_SERVICE))
    body["Actions"].pop("#DellLCService.SupportAssistExportLastCollection")
    _overlay_lc_service(service, body)

    result = manager.sync_invoke(
        ApiRequestType.DellLcSupportAssistExport,
        "dell-lc-supportassist-export",
        share_type="NFS",
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "action '#DellLCService.SupportAssistExportLastCollection' not found"
    )
    assert result.data["action"] == (
        "#DellLCService.SupportAssistExportLastCollection"
    )
    assert LC_SERVICE in result.data["checked"]
    assert _post_requests(service) == []


def test_supportassist_export_is_registered_and_classified():
    """The command is registered and classified as guarded."""
    registry = IDracManager().get_registry()
    assert registry[ApiRequestType.DellLcSupportAssistExport][
        "dell-lc-supportassist-export"
    ] is DellLcSupportAssistExport

    assert classify("#DellLCService.SupportAssistExportLastCollection") is (
        Destructiveness.DESTRUCTIVE
    )
