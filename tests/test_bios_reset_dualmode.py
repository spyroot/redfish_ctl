"""Dual-mode-style coverage for the guarded BIOS reset command."""

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
