"""Dual-mode-style tests for Dell LC ePSA diagnostics."""

import copy

import pytest

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.dell_lc.cmd_dell_lc_epsa_diagnostics import DellLcEpsaDiagnostics
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_manager_shared import ApiRequestType

LC_SERVICE_PATH = "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellLCService"
EPSA_TARGET = f"{LC_SERVICE_PATH}/Actions/DellLCService.RunePSADiagnostics"


def _post_requests(service):
    """Return POST requests recorded by the mock service."""
    return [request for request in service.requests if request.method == "POST"]


def _install_lc_service(redfish_service, body=None):
    """Overlay a DellLCService fixture that advertises RunePSADiagnostics."""
    service = body or {
        "@odata.id": LC_SERVICE_PATH,
        "@odata.type": "#DellLCService.v1_5_0.DellLCService",
        "Id": "DellLCService",
        "Name": "Dell Lifecycle Controller Service",
        "Actions": {
            "#DellLCService.RunePSADiagnostics": {
                "RunMode@Redfish.AllowableValues": [
                    "Express",
                    "ExpressAndExtended",
                    "Extended",
                ],
                "RebootJobType@Redfish.AllowableValues": [
                    "GracefulRebootWithForcedShutdown",
                    "GracefulRebootWithoutForcedShutdown",
                    "PowerCycle",
                ],
                "target": EPSA_TARGET,
            }
        },
    }
    manager = {
        "@odata.id": "/redfish/v1/Managers/iDRAC.Embedded.1",
        "Id": "iDRAC.Embedded.1",
        "Oem": {
            "Dell": {
                "DellLCService": {"@odata.id": LC_SERVICE_PATH},
            }
        },
    }
    redfish_service._overlay[LC_SERVICE_PATH] = service
    redfish_service._overlay[LC_SERVICE_PATH.lower()] = service
    redfish_service._overlay["/redfish/v1/Managers/iDRAC.Embedded.1"] = manager
    redfish_service._overlay["/redfish/v1/managers/idrac.embedded.1"] = manager
    return service


def test_epsa_diagnostics_defaults_to_dry_run(redfish_mock, redfish_service):
    """dell-lc-epsa-diagnostics previews by default and never POSTs."""
    _install_lc_service(redfish_service)

    result = redfish_mock.sync_invoke(
        ApiRequestType.DellLcEpsaDiagnostics,
        "dell-lc-epsa-diagnostics",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == "#DellLCService.RunePSADiagnostics"
    assert result.data["target"] == EPSA_TARGET
    assert result.data["payload"] == {
        "RunMode": "Express",
        "RebootJobType": "GracefulRebootWithoutForcedShutdown",
    }
    assert result.data["level"] == "destructive"
    assert result.data["blocked"] == "destructive action requires --confirm"
    assert _post_requests(redfish_service) == []


def test_epsa_confirm_without_reboot_ack_still_does_not_post(
    redfish_mock,
    redfish_service,
):
    """--confirm alone is not enough for a diagnostic action that can reboot."""
    _install_lc_service(redfish_service)

    result = redfish_mock.sync_invoke(
        ApiRequestType.DellLcEpsaDiagnostics,
        "dell-lc-epsa-diagnostics",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] == (
        "ePSA diagnostics requires --confirm and --i-understand-reboot"
    )
    assert _post_requests(redfish_service) == []


def test_epsa_confirm_with_reboot_ack_posts_payload(redfish_mock, redfish_service):
    """Both explicit flags allow one POST to the discovered Dell LC target."""
    _install_lc_service(redfish_service)

    result = redfish_mock.sync_invoke(
        ApiRequestType.DellLcEpsaDiagnostics,
        "dell-lc-epsa-diagnostics",
        run_mode="Extended",
        reboot_job_type="PowerCycle",
        confirm=True,
        confirm_reboot=True,
    )

    posts = [
        request
        for request in _post_requests(redfish_service)
        if request.path.lower() == EPSA_TARGET.lower()
    ]
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["target"] == EPSA_TARGET
    assert result.data["level"] == "destructive"
    assert len(posts) == 1
    assert posts[0].json() == {
        "RunMode": "Extended",
        "RebootJobType": "PowerCycle",
    }


def test_epsa_dry_run_overrides_both_confirmation_flags(
    redfish_mock,
    redfish_service,
):
    """--dry_run remains a no-POST preview even with both confirmation flags."""
    _install_lc_service(redfish_service)

    result = redfish_mock.sync_invoke(
        ApiRequestType.DellLcEpsaDiagnostics,
        "dell-lc-epsa-diagnostics",
        confirm=True,
        confirm_reboot=True,
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] is None
    assert _post_requests(redfish_service) == []


def test_epsa_invalid_allowable_value_reports_without_post(
    redfish_mock,
    redfish_service,
):
    """Advertised RunMode values are enforced before any POST occurs."""
    _install_lc_service(redfish_service)

    result = redfish_mock.sync_invoke(
        ApiRequestType.DellLcEpsaDiagnostics,
        "dell-lc-epsa-diagnostics",
        run_mode="Full",
        confirm=True,
        confirm_reboot=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "invalid value for DellLCService.RunePSADiagnostics RunMode: "
        "Full; allowed: Express, ExpressAndExtended, Extended"
    )
    assert _post_requests(redfish_service) == []


def test_epsa_missing_action_reports_blocker(redfish_mock, redfish_service):
    """A DellLCService without the action fails closed before POST."""
    service = _install_lc_service(redfish_service)
    missing = copy.deepcopy(service)
    missing["Actions"].pop("#DellLCService.RunePSADiagnostics")
    redfish_service._overlay[LC_SERVICE_PATH] = missing
    redfish_service._overlay[LC_SERVICE_PATH.lower()] = missing

    with pytest.raises(InvalidArgument, match="no DellLCService exposing"):
        redfish_mock.sync_invoke(
            ApiRequestType.DellLcEpsaDiagnostics,
            "dell-lc-epsa-diagnostics",
            confirm=True,
            confirm_reboot=True,
        )
    assert _post_requests(redfish_service) == []


def test_epsa_rejects_blank_payload_values() -> None:
    """Required payload values must not be blank."""
    with pytest.raises(InvalidArgument, match="run mode cannot be empty"):
        DellLcEpsaDiagnostics._payload(" ", "PowerCycle")
    with pytest.raises(InvalidArgument, match="reboot job type cannot be empty"):
        DellLcEpsaDiagnostics._payload("Express", "")


def test_epsa_command_is_registered() -> None:
    """The command is wired into the registry and parser."""
    registry = RedfishManagerBase().get_registry()
    assert (
        registry[ApiRequestType.DellLcEpsaDiagnostics]["dell-lc-epsa-diagnostics"]
        is DellLcEpsaDiagnostics
    )

    cmd_parser, cmd_name, cmd_help = DellLcEpsaDiagnostics.register_subcommand(
        DellLcEpsaDiagnostics
    )
    help_text = cmd_parser.format_help()

    assert cmd_name == "dell-lc-epsa-diagnostics"
    assert "ePSA" in cmd_help
    assert "--confirm" in help_text
    assert "--i-understand-reboot" in help_text
