"""Dual-mode-style coverage for LicenseService.Install."""

from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

DELL_CORPUS = corpus_dir(
    Path(__file__).parent.parent / "dell_xr8620t_corpus.tar.gz", "10.252.252.209"
)
LICENSE_SERVICE = "/redfish/v1/LicenseService"
INSTALL_TARGET = f"{LICENSE_SERVICE}/Actions/LicenseService.Install"


@pytest.fixture
def dell_license_install_mock():
    """Return a manager and mock service backed by the Dell XR8620t corpus.

    The vendor-faithful service realizes an Action POST the Dell way: 202 plus
    a ``JID_`` OEM job id in the Location header, never a DMTF-generic token.

    :return: tuple of IDracManager and the recording MockRedfishService.
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
                idrac_ip="mock-dell-license",
                idrac_username="root",
                idrac_password="mock",
                insecure=True,
                is_debug=False,
            ),
            service,
        )


def _post_requests(service):
    """Return POST requests recorded by the mock Redfish service.

    :param service: the recording MockRedfishService.
    :return: list of POST requests.
    """
    return [request for request in service.requests if request.method == "POST"]


def test_license_install_lists_target_without_mutating(dell_license_install_mock):
    """With no license URI, the command lists the Install target and never POSTs."""
    manager, service = dell_license_install_mock

    result = manager.sync_invoke(ApiRequestType.LicenseInstall, "license-install")

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == {
        "license_service": LICENSE_SERVICE,
        "action": "#LicenseService.Install",
        "target": INSTALL_TARGET,
        "transfer_protocols": ["CIFS", "HTTP", "HTTPS", "NFS"],
    }
    assert _post_requests(service) == []


def test_license_install_without_confirm_is_preview_only(dell_license_install_mock):
    """LicenseService.Install resolves the target but does not POST without --confirm."""
    manager, service = dell_license_install_mock

    result = manager.sync_invoke(
        ApiRequestType.LicenseInstall,
        "license-install",
        license_file_uri="https://repo.example.test/license.xml",
        transfer_protocol="HTTPS",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == "#LicenseService.Install"
    assert result.data["target"] == INSTALL_TARGET
    assert result.data["level"] == "destructive"
    assert result.data["blocked"] == "destructive action requires --confirm"
    assert result.data["payload"] == {
        "LicenseFileURI": "https://repo.example.test/license.xml",
        "TransferProtocol": "HTTPS",
    }
    assert _post_requests(service) == []


def test_license_install_confirm_posts_payload(dell_license_install_mock):
    """--confirm POSTs the license URI payload to the discovered action target."""
    manager, service = dell_license_install_mock

    result = manager.sync_invoke(
        ApiRequestType.LicenseInstall,
        "license-install",
        license_file_uri="https://repo.example.test/license.xml",
        transfer_protocol="HTTPS",
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#LicenseService.Install"
    assert result.data["target"] == INSTALL_TARGET
    assert result.data["level"] == "destructive"
    assert result.data["task_id"] == service.JOB_ID
    assert service.JOB_ID.startswith("JID_")
    assert len(posts) == 1
    assert posts[0].path.lower() == INSTALL_TARGET.lower()
    assert posts[0].json() == {
        "LicenseFileURI": "https://repo.example.test/license.xml",
        "TransferProtocol": "HTTPS",
    }


def test_license_install_confirm_dry_run_still_does_not_post(dell_license_install_mock):
    """--dry_run remains a no-POST preview even when --confirm is also present."""
    manager, service = dell_license_install_mock

    result = manager.sync_invoke(
        ApiRequestType.LicenseInstall,
        "license-install",
        license_file_uri="https://repo.example.test/license.xml",
        transfer_protocol="HTTPS",
        confirm=True,
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] is None
    assert result.data["target"] == INSTALL_TARGET
    assert _post_requests(service) == []


def test_license_install_rejects_invalid_transfer_protocol(dell_license_install_mock):
    """Inline allowable values reject an unsupported TransferProtocol before POST."""
    manager, service = dell_license_install_mock

    result = manager.sync_invoke(
        ApiRequestType.LicenseInstall,
        "license-install",
        license_file_uri="https://repo.example.test/license.xml",
        transfer_protocol="FTP",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "invalid value for LicenseService.Install TransferProtocol: FTP; "
        "allowed: CIFS, HTTP, HTTPS, NFS"
    )
    assert result.data["validation_errors"] == [
        {
            "parameter": "TransferProtocol",
            "value": "FTP",
            "allowed": ["CIFS", "HTTP", "HTTPS", "NFS"],
        }
    ]
    assert _post_requests(service) == []


def test_license_install_strips_and_omits_empty_optional_fields(dell_license_install_mock):
    """Optional strings are stripped, and blank values are omitted from payloads."""
    manager, service = dell_license_install_mock

    result = manager.sync_invoke(
        ApiRequestType.LicenseInstall,
        "license-install",
        license_file_uri="https://repo.example.test/license.xml",
        transfer_protocol=" HTTPS ",
        license_username="  ",
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["payload"] == {
        "LicenseFileURI": "https://repo.example.test/license.xml",
        "TransferProtocol": "HTTPS",
    }
    assert _post_requests(service) == []


def test_license_install_masks_password_from_env_in_dry_run(
    dell_license_install_mock,
    monkeypatch,
):
    """Dry-run output does not echo a URI credential password read from env."""
    manager, service = dell_license_install_mock
    monkeypatch.setenv("LICENSE_INSTALL_PASSWORD", "placeholder-value")

    result = manager.sync_invoke(
        ApiRequestType.LicenseInstall,
        "license-install",
        license_file_uri="https://repo.example.test/license.xml",
        license_username="license-reader",
        license_password_env="LICENSE_INSTALL_PASSWORD",
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["payload"]["Username"] == "license-reader"
    assert result.data["payload"]["Password"] == "********"
    assert _post_requests(service) == []


def test_license_install_reads_password_file_and_redacts_dry_run(
    dell_license_install_mock,
    tmp_path,
):
    """A password file source is supported without echoing the file content."""
    manager, service = dell_license_install_mock
    password_file = tmp_path / "license-password"
    password_file.write_text("placeholder-value\n", encoding="utf-8")

    result = manager.sync_invoke(
        ApiRequestType.LicenseInstall,
        "license-install",
        license_file_uri="https://repo.example.test/license.xml",
        license_password_file=str(password_file),
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["payload"]["Password"] == "********"
    assert _post_requests(service) == []


def test_license_install_rejects_missing_password_env(dell_license_install_mock):
    """Missing password environment variables fail before any POST."""
    manager, service = dell_license_install_mock

    with pytest.raises(
        InvalidArgument,
        match="environment variable 'MISSING_LICENSE_PASSWORD'",
    ):
        manager.sync_invoke(
            ApiRequestType.LicenseInstall,
            "license-install",
            license_file_uri="https://repo.example.test/license.xml",
            license_password_env="MISSING_LICENSE_PASSWORD",
            confirm=True,
        )

    assert _post_requests(service) == []


def test_license_install_rejects_empty_license_uri(dell_license_install_mock):
    """A blank URI is rejected before any action POST can fire."""
    manager, service = dell_license_install_mock

    with pytest.raises(InvalidArgument, match="license file URI cannot be empty"):
        manager.sync_invoke(
            ApiRequestType.LicenseInstall,
            "license-install",
            license_file_uri="   ",
            confirm=True,
        )

    assert _post_requests(service) == []


def test_license_install_reports_missing_action_without_post(redfish_mock_factory):
    """A LicenseService without Install reports the available actions."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.LicenseInstall,
        "license-install",
        license_file_uri="https://repo.example.test/license.xml",
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "action '#LicenseService.Install' not found on /redfish/v1/LicenseService"
    )
    assert result.data == {
        "action": "#LicenseService.Install",
        "available": [],
    }
    assert _post_requests(service) == []
