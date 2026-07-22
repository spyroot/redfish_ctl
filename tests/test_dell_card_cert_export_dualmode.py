"""Dual-mode tests for Dell iDRAC card certificate export actions."""
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.actions.action_policy import Destructiveness, classify
from redfish_ctl.oem.cmd_dell_card_cert_export import DellCardCertExport
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType

REPO_ROOT = Path(__file__).resolve().parents[1]
DELL_CORPUS = corpus_dir(
    REPO_ROOT / "tests" / "dell_xr8620t_corpus.tar.gz",
    "10.252.252.209",
)
CARD_SERVICE = (
    "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DelliDRACCardService"
)
ACTION_BASE = f"{CARD_SERVICE}/Actions/DelliDRACCardService"


@pytest.fixture
def dell_corpus_mock():
    """Return a manager and mock service backed by the Dell XR8620t corpus."""
    requests_mock = pytest.importorskip("requests_mock")
    service = MockRedfishService(DELL_CORPUS, index=_build_fixture_index(DELL_CORPUS))
    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=service.get_cb)
        mocker.patch(requests_mock.ANY, text=service.patch_cb)
        mocker.post(requests_mock.ANY, text=service.post_cb)
        mocker.delete(requests_mock.ANY, text=service.delete_cb)
        service.mocker = mocker
        yield (
            IDracManager(
                idrac_ip="mock-dell-xr8620t",
                idrac_username="root",
                idrac_password="mock",
                insecure=True,
                is_debug=False,
            ),
            service,
        )


def _post_requests(service):
    """Return POST requests recorded by the mock Redfish service."""
    return [request for request in service.requests if request.method == "POST"]


def test_dell_card_cert_export_lists_corpus_targets_without_post(dell_corpus_mock):
    """Listing discovers Dell card certificate export actions."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellCardCertExport,
        "dell-card-cert-export",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    actions = {row["Action"]: row for row in result.data}
    assert set(actions) == {
        "export-cert",
        "export-ssl-cert",
        "factory-identity",
    }
    assert actions["export-cert"]["Resource"] == CARD_SERVICE
    assert actions["export-cert"]["Target"] == (
        f"{ACTION_BASE}.ExportCertificate"
    )
    assert actions["export-ssl-cert"]["Target"] == (
        f"{ACTION_BASE}.ExportSSLCertificate"
    )
    assert actions["factory-identity"]["Target"] == (
        f"{ACTION_BASE}.FactoryIdentityExportCertificate"
    )
    assert _post_requests(service) == []


def test_dell_card_cert_export_previews_selected_action(dell_corpus_mock):
    """A selected export action resolves target and payload without POSTing."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellCardCertExport,
        "dell-card-cert-export",
        action="export-cert",
        certificate_type="KMS_SERVER_CA",
    )

    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == "#DelliDRACCardService.ExportCertificate"
    assert result.data["level"] == "read_only"
    assert result.data["target"] == f"{ACTION_BASE}.ExportCertificate"
    assert result.data["payload"] == {"CertificateType": "KMS_SERVER_CA"}
    assert result.data["blocked"] is None
    assert _post_requests(service) == []


def test_dell_card_cert_export_query_posts_selected_target(dell_corpus_mock):
    """--query POSTs exactly one selected read-only export action."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellCardCertExport,
        "dell-card-cert-export",
        action="export-ssl-cert",
        ssl_cert_type="Server",
        query=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#DelliDRACCardService.ExportSSLCertificate"
    assert result.data["level"] == "read_only"
    assert len(posts) == 1
    assert posts[0].path.lower() == f"{ACTION_BASE}.ExportSSLCertificate".lower()
    assert posts[0].json() == {"SSLCertType": "Server"}


def test_dell_card_cert_export_requires_payload_selector(dell_corpus_mock):
    """Actions with typed payloads report the missing CLI selector."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellCardCertExport,
        "dell-card-cert-export",
        action="export-cert",
        query=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == "export-cert requires --certificate-type"
    assert _post_requests(service) == []


def test_dell_card_cert_export_validates_allowable_values(dell_corpus_mock):
    """Invalid certificate type values are rejected before POST."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellCardCertExport,
        "dell-card-cert-export",
        action="export-cert",
        certificate_type="Server",
        query=True,
    )

    assert result.error == (
        "invalid value for DelliDRACCardService.ExportCertificate "
        "CertificateType: Server; allowed: KMS_SERVER_CA, SEKM_SSL_CERT"
    )
    assert result.data["validation_errors"][0]["parameter"] == "CertificateType"
    assert _post_requests(service) == []


def test_dell_card_cert_export_factory_identity_has_empty_payload(dell_corpus_mock):
    """The factory identity export action has no payload selector."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellCardCertExport,
        "dell-card-cert-export",
        action="factory-identity",
    )

    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["payload"] == {}
    assert result.data["target"] == (
        f"{ACTION_BASE}.FactoryIdentityExportCertificate"
    )
    assert _post_requests(service) == []


def test_dell_card_cert_export_missing_target_reports_without_post(
    redfish_mock_factory,
):
    """A fixture without Dell card export resources reports an error."""
    manager, service = redfish_mock_factory("generic")

    result = manager.sync_invoke(
        ApiRequestType.DellCardCertExport,
        "dell-card-cert-export",
        action="factory-identity",
        query=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "Dell iDRAC card certificate export action not found: factory-identity"
    )
    assert result.data == {"available": []}
    assert _post_requests(service) == []


def test_dell_card_cert_export_exposes_cli_entrypoint():
    """The dell-card-cert-export command is wired into the command registry."""
    registry = IDracManager().get_registry()
    assert registry[ApiRequestType.DellCardCertExport][
        "dell-card-cert-export"
    ] is DellCardCertExport

    cmd_parser, cmd_name, cmd_help = DellCardCertExport.register_subcommand(
        DellCardCertExport
    )
    help_text = cmd_parser.format_help()

    assert classify(
        "#DelliDRACCardService.FactoryIdentityExportCertificate"
    ) is Destructiveness.READ_ONLY
    assert "--action" in help_text
    assert "--query" in help_text
    assert "--certificate-type" in help_text
    assert "--ssl-cert-type" in help_text
    assert cmd_name == "dell-card-cert-export"
    assert "Dell" in cmd_help
