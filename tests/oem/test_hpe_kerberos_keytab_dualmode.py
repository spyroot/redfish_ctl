"""Dual-mode tests for HPE iLO Kerberos keytab import."""
from __future__ import annotations

import base64
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.oem.cmd_hpe_kerberos_keytab import HpeKerberosKeytabImport
from redfish_ctl.redfish_manager import CommandResult

REPO_ROOT = Path(__file__).resolve().parents[2]
HPE_CORPUS = corpus_dir(REPO_ROOT / "tests" / "hpe_dl360_corpus.tar.gz", "10.43.3.209")
ACCOUNT_SERVICE = "/redfish/v1/AccountService"
IMPORT_ACTION = "#HpeiLOAccountService.ImportKerberosKeytab"
IMPORT_TARGET = (
    "/redfish/v1/AccountService/Actions/Oem/Hpe/"
    "HpeiLOAccountService.ImportKerberosKeytab"
)


@pytest.fixture
def hpe_keytab_mock():
    """Return a manager and mock service backed by the full HPE DL360 corpus.

    :return: tuple of Redfish manager and mock service.
    """
    requests_mock = pytest.importorskip("requests_mock")
    service = MockRedfishService(
        HPE_CORPUS, index=_build_fixture_index(HPE_CORPUS), vendor="hpe")
    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=service.get_cb)
        mocker.patch(requests_mock.ANY, text=service.patch_cb)
        mocker.post(requests_mock.ANY, text=service.post_cb)
        mocker.delete(requests_mock.ANY, text=service.delete_cb)
        service.mocker = mocker
        yield (
            IDracManager(
                idrac_ip="mock-hpe-keytab",
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


def test_hpe_keytab_import_lists_target_without_post(hpe_keytab_mock):
    """With no keytab source, the command lists the target and never POSTs."""
    manager, service = hpe_keytab_mock

    result = manager.sync_invoke(
        ApiRequestType.HpeKerberosKeytabImport,
        "hpe-kerberos-keytab-import",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == {
        "account_service": ACCOUNT_SERVICE,
        "action": IMPORT_ACTION,
        "target": IMPORT_TARGET,
        "payload_field": "KerberosKeytab",
    }
    assert _post_requests(service) == []


def test_hpe_keytab_import_file_preview_redacts_payload(
    hpe_keytab_mock,
    tmp_path,
):
    """A keytab file previews a redacted destructive payload by default."""
    manager, service = hpe_keytab_mock
    keytab = tmp_path / "krb5.keytab"
    keytab.write_bytes(b"\x05\x02placeholder-keytab")

    result = manager.sync_invoke(
        ApiRequestType.HpeKerberosKeytabImport,
        "hpe-kerberos-keytab-import",
        keytab_file=str(keytab),
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == IMPORT_ACTION
    assert result.data["target"] == IMPORT_TARGET
    assert result.data["level"] == "destructive"
    assert result.data["blocked"] == "destructive action requires --confirm"
    assert result.data["payload"] == {"KerberosKeytab": "********"}
    assert "placeholder-keytab" not in str(result.data)
    assert _post_requests(service) == []


def test_hpe_keytab_import_confirm_posts_encoded_file(
    hpe_keytab_mock,
    tmp_path,
):
    """--confirm POSTs the Base64-encoded keytab payload to the discovered target."""
    manager, service = hpe_keytab_mock
    raw_keytab = b"\x05\x02placeholder-keytab"
    keytab = tmp_path / "krb5.keytab"
    keytab.write_bytes(raw_keytab)

    result = manager.sync_invoke(
        ApiRequestType.HpeKerberosKeytabImport,
        "hpe-kerberos-keytab-import",
        keytab_file=str(keytab),
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == IMPORT_ACTION
    assert result.data["target"] == IMPORT_TARGET
    assert result.data["level"] == "destructive"
    assert len(posts) == 1
    assert posts[0].path.lower() == IMPORT_TARGET.lower()
    assert posts[0].json() == {
        "KerberosKeytab": base64.b64encode(raw_keytab).decode("ascii")
    }


def test_hpe_keytab_import_env_preview_redacts_payload(
    hpe_keytab_mock,
    monkeypatch,
):
    """Base64 keytab text can come from env without echoing the value."""
    manager, service = hpe_keytab_mock
    encoded = base64.b64encode(b"env-keytab").decode("ascii")
    monkeypatch.setenv("HPE_KEYTAB_B64", encoded)

    result = manager.sync_invoke(
        ApiRequestType.HpeKerberosKeytabImport,
        "hpe-kerberos-keytab-import",
        keytab_base64_env="HPE_KEYTAB_B64",
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["payload"] == {"KerberosKeytab": "********"}
    assert encoded not in str(result.data)
    assert _post_requests(service) == []


def test_hpe_keytab_import_rejects_missing_env(hpe_keytab_mock):
    """Missing env source fails before any POST."""
    manager, service = hpe_keytab_mock

    with pytest.raises(InvalidArgument, match="MISSING_HPE_KEYTAB"):
        manager.sync_invoke(
            ApiRequestType.HpeKerberosKeytabImport,
            "hpe-kerberos-keytab-import",
            keytab_base64_env="MISSING_HPE_KEYTAB",
            confirm=True,
        )

    assert _post_requests(service) == []


def test_hpe_keytab_import_rejects_invalid_base64_env(
    hpe_keytab_mock,
    monkeypatch,
):
    """The env path validates that its value is Base64 text."""
    manager, service = hpe_keytab_mock
    monkeypatch.setenv("HPE_KEYTAB_B64", "not base64")

    with pytest.raises(InvalidArgument, match="Base64-encoded keytab"):
        manager.sync_invoke(
            ApiRequestType.HpeKerberosKeytabImport,
            "hpe-kerberos-keytab-import",
            keytab_base64_env="HPE_KEYTAB_B64",
            confirm=True,
        )

    assert _post_requests(service) == []


def test_hpe_keytab_import_missing_target_reports_without_post(
    redfish_mock_factory,
    tmp_path,
):
    """A non-HPE fixture reports the missing action and does not POST."""
    manager, service = redfish_mock_factory("generic")
    keytab = tmp_path / "krb5.keytab"
    keytab.write_bytes(b"\x05\x02placeholder-keytab")

    result = manager.sync_invoke(
        ApiRequestType.HpeKerberosKeytabImport,
        "hpe-kerberos-keytab-import",
        keytab_file=str(keytab),
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "action '#HpeiLOAccountService.ImportKerberosKeytab' not found on "
        "/redfish/v1/AccountService"
    )
    assert result.data["action"] == IMPORT_ACTION
    assert _post_requests(service) == []


def test_hpe_keytab_import_exposes_cli_entrypoint():
    """The HPE Kerberos keytab command is wired into the package registry."""
    registry = IDracManager().get_registry()
    assert registry[ApiRequestType.HpeKerberosKeytabImport][
        "hpe-kerberos-keytab-import"
    ] is HpeKerberosKeytabImport

    cmd_parser, cmd_name, cmd_help = HpeKerberosKeytabImport.register_subcommand(
        HpeKerberosKeytabImport
    )

    help_text = cmd_parser.format_help()
    assert "--keytab-base64-env" in help_text
    assert "--keytab-file" in help_text
    assert cmd_name == "hpe-kerberos-keytab-import"
    assert "Kerberos" in cmd_help
