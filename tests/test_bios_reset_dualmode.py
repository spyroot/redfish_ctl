"""Dual-mode-style coverage for the guarded BIOS reset command."""

import copy

import pytest

from redfish_ctl.bios.cmd_bios_reset_default import BiosResetDefault
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.redfish_manager_shared import ApiRequestType


def _post_requests(service):
    """Return POST requests recorded by the mock Redfish service."""
    return [request for request in service.requests if request.method == "POST"]


def test_bios_reset_without_confirm_dry_runs_and_does_not_post(
    redfish_mock_factory,
) -> None:
    """bios-reset previews the discovered GB300 target unless confirmed."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(ApiRequestType.BiosResetDefault, "bios_reset")

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] == "destructive action requires --confirm"
    assert result.data["action"] == "#Bios.ResetBios"
    assert result.data["target"] == (
        "/redfish/v1/Systems/System_0/Bios/Actions/Bios.ResetBios"
    )
    assert _post_requests(service) == []


def test_bios_reset_confirm_posts_to_dell_discovered_target(
    redfish_mock,
    redfish_service,
) -> None:
    """bios-reset --confirm POSTs to the Dell BIOS action target."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.BiosResetDefault,
        "bios_reset",
        confirm=True,
    )

    posts = _post_requests(redfish_service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["target"] == (
        "/redfish/v1/Systems/System.Embedded.1/Bios/Settings/Actions/Bios.ResetBios"
    )
    assert result.data["level"] == "destructive"
    assert len(posts) == 1
    assert posts[0].path.lower() == (
        "/redfish/v1/systems/system.embedded.1/bios/settings/actions/bios.resetbios"
    )
    assert posts[0].json() == {}


def test_bios_reset_confirm_posts_optional_reset_type(
    redfish_mock,
    redfish_service,
) -> None:
    """bios-reset includes ResetType only when requested."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.BiosResetDefault,
        "bios_reset",
        confirm=True,
        reset_type="ResetAll",
    )

    posts = _post_requests(redfish_service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert len(posts) == 1
    assert posts[0].path.lower() == (
        "/redfish/v1/systems/system.embedded.1/bios/settings/actions/bios.resetbios"
    )
    assert posts[0].json() == {"ResetType": "ResetAll"}


def test_bios_reset_confirm_dry_run_still_does_not_post(
    redfish_mock,
    redfish_service,
) -> None:
    """--dry_run wins over --confirm so operators can preview a confirmed call."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.BiosResetDefault,
        "bios_reset",
        confirm=True,
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] is None
    assert result.data["target"] == (
        "/redfish/v1/Systems/System.Embedded.1/Bios/Settings/Actions/Bios.ResetBios"
    )
    assert _post_requests(redfish_service) == []


def test_bios_reset_confirm_posts_to_hpe_discovered_target(
    redfish_mock_factory,
) -> None:
    """bios-reset --confirm POSTs to the HPE BIOS action target."""
    manager, service = redfish_mock_factory("hpe")

    result = manager.sync_invoke(
        ApiRequestType.BiosResetDefault,
        "bios_reset",
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["target"] == (
        "/redfish/v1/systems/1/bios/Actions/Bios.ResetBios/"
    )
    assert result.data["level"] == "destructive"
    assert len(posts) == 1
    assert posts[0].path.lower() == (
        "/redfish/v1/systems/1/bios/actions/bios.resetbios/"
    )
    assert posts[0].json() == {}


def test_bios_reset_fallback_uri_normalizes_bios_fragment() -> None:
    """Fallback URI joining stays valid even if the API fragment shape changes."""
    assert BiosResetDefault._bios_fallback_uri("/redfish/v1/Systems/1") == (
        "/redfish/v1/Systems/1/Bios"
    )
    assert BiosResetDefault._bios_fallback_uri("/redfish/v1/Systems/1/") == (
        "/redfish/v1/Systems/1/Bios"
    )


def test_bios_reset_system_query_failure_is_not_hidden(monkeypatch) -> None:
    """Connectivity/auth/parsing failures on the ComputerSystem read propagate."""
    command = BiosResetDefault(
        idrac_ip="127.0.0.1",
        idrac_username="user",
        idrac_password="password",
        idrac_port=443,
        insecure=True,
        is_http=True,
    )
    command.__dict__["idrac_manage_servers"] = "/redfish/v1/Systems/1"

    def fail_query(*_args, **_kwargs):
        raise RuntimeError("system read failed")

    monkeypatch.setattr(command, "base_query", fail_query)

    with pytest.raises(RuntimeError, match="system read failed"):
        command._bios_uri(do_async=False)


def test_bios_reset_missing_action_reports_available_without_post(
    redfish_mock_factory,
) -> None:
    """A BIOS resource without ResetBios returns an error and never POSTs."""
    manager, service = redfish_mock_factory("supermicro")
    bios_path = "/redfish/v1/Systems/System_0/Bios"
    bios = copy.deepcopy(service._state(bios_path))
    bios["Actions"].pop("#Bios.ResetBios")
    service._overlay[bios_path] = bios
    service._overlay[bios_path.lower()] = bios

    result = manager.sync_invoke(
        ApiRequestType.BiosResetDefault,
        "bios_reset",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "action '#Bios.ResetBios' not found on "
        "/redfish/v1/Systems/System_0/Bios"
    )
    assert result.data["action"] == "#Bios.ResetBios"
    assert "ResetBios" not in result.data["available"]
    assert "#Bios.ResetBios" not in result.data["available"]
    assert _post_requests(service) == []
