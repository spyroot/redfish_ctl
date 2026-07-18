"""Dual-mode tests for Dell card-service CSR generation actions."""

from redfish_ctl.actions.action_policy import classify
from redfish_ctl.oem.cmd_dell_card_csr import DellCardCsr
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_manager_shared import ApiRequestType

SERVICE_URI = "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DelliDRACCardService"
FACTORY_ACTION = "#DelliDRACCardService.FactoryIdentityCertificateGenerateCSR"
FACTORY_TARGET = (
    "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/"
    "DelliDRACCardService/Actions/"
    "DelliDRACCardService.FactoryIdentityCertificateGenerateCSR"
)
SEKM_ACTION = "#DelliDRACCardService.GenerateSEKMCSR"
SEKM_TARGET = (
    "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/"
    "DelliDRACCardService/Actions/DelliDRACCardService.GenerateSEKMCSR"
)


def _post_requests(service):
    """Return POST requests recorded by the offline mock service."""
    return [request for request in service.requests if request.method == "POST"]


def _set_card_service(service, body):
    """Overlay the Dell card-service fixture under canonical and lowercase paths."""
    service._overlay[SERVICE_URI] = body
    service._overlay[SERVICE_URI.lower()] = body


def test_dell_card_csr_lists_corpus_backed_targets(redfish_api, redfish_service):
    """dell-card-csr lists CSR targets from the Dell card-service fixture."""
    result = redfish_api.sync_invoke(
        ApiRequestType.DellCardCsr,
        "dell-card-csr",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["csr_targets"] == [
        {
            "Action": "factory-identity",
            "FullType": FACTORY_ACTION,
            "Resource": SERVICE_URI,
            "Target": FACTORY_TARGET,
            "Description": (
                "generate a CSR for the Dell factory identity certificate"
            ),
        },
        {
            "Action": "sekm",
            "FullType": SEKM_ACTION,
            "Resource": SERVICE_URI,
            "Target": SEKM_TARGET,
            "Description": "generate a CSR for Dell SEKM certificate enrollment",
        },
    ]
    assert _post_requests(redfish_service) == []


def test_dell_card_csr_defaults_to_preview(redfish_api, redfish_service):
    """A selected CSR action does not POST unless --confirm is supplied."""
    result = redfish_api.sync_invoke(
        ApiRequestType.DellCardCsr,
        "dell-card-csr",
        action="sekm",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == SEKM_ACTION
    assert result.data["target"] == SEKM_TARGET
    assert result.data["payload"] == {}
    assert result.data["level"] == "reversible"
    assert result.data["requires_confirm"] is True
    assert result.data["blocked"] == (
        "Dell card-service CSR generation requires --confirm"
    )
    assert _post_requests(redfish_service) == []


def test_dell_card_csr_confirm_posts_empty_payload(redfish_api, redfish_service):
    """With --confirm the command POSTs to the discovered CSR action target."""
    result = redfish_api.sync_invoke(
        ApiRequestType.DellCardCsr,
        "dell-card-csr",
        action="factory-identity",
        confirm=True,
    )

    posts = _post_requests(redfish_service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == FACTORY_ACTION
    assert result.data["target"] == FACTORY_TARGET
    assert result.data["level"] == "reversible"
    assert len(posts) == 1
    assert posts[0].path == FACTORY_TARGET.lower()
    assert posts[0].json() == {}


def test_dell_card_csr_dry_run_overrides_confirm(redfish_api, redfish_service):
    """--dry_run suppresses POST even when --confirm is also supplied."""
    result = redfish_api.sync_invoke(
        ApiRequestType.DellCardCsr,
        "dell-card-csr",
        action="factory-identity",
        confirm=True,
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] is None
    assert _post_requests(redfish_service) == []


def test_dell_card_csr_reports_missing_action(redfish_api, redfish_service):
    """A card-service resource without the selected CSR action reports available rows."""
    body = dict(redfish_service._state(SERVICE_URI))
    body["Actions"] = {
        FACTORY_ACTION: {
            "target": FACTORY_TARGET,
        },
    }
    _set_card_service(redfish_service, body)

    result = redfish_api.sync_invoke(
        ApiRequestType.DellCardCsr,
        "dell-card-csr",
        action="sekm",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == "Dell card-service CSR action not found: sekm"
    assert result.data["action"] == SEKM_ACTION
    assert result.data["available"] == [
        {
            "Action": "factory-identity",
            "FullType": FACTORY_ACTION,
            "Resource": SERVICE_URI,
            "Target": FACTORY_TARGET,
            "Description": (
                "generate a CSR for the Dell factory identity certificate"
            ),
        },
    ]
    assert _post_requests(redfish_service) == []


def test_dell_card_csr_registration_and_policy():
    """The command is registered and the CSR actions are reversible."""
    registry = RedfishManagerBase().get_registry()
    assert registry[ApiRequestType.DellCardCsr]["dell-card-csr"] is DellCardCsr
    assert classify(FACTORY_ACTION).value == "reversible"
    assert classify(SEKM_ACTION).value == "reversible"

    cmd_parser, cmd_name, cmd_help = DellCardCsr.register_subcommand(DellCardCsr)
    help_text = cmd_parser.format_help()

    assert cmd_name == "dell-card-csr"
    assert "card-service CSRs" in cmd_help
    assert "--action" in help_text
    assert "--confirm" in help_text
    assert "--dry_run" in help_text
