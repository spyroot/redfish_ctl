"""Dual-mode-style coverage for the guarded BIOS ChangePassword command."""

import copy

import pytest

from redfish_ctl.bios.cmd_bios_change_password import BiosChangePassword
from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.redfish_manager_shared import ApiRequestType


def _post_requests(service):
    """Return POST requests recorded by the mock Redfish service."""
    return [request for request in service.requests if request.method == "POST"]


def test_bios_change_password_lists_target_without_mutating(
    redfish_mock_factory,
) -> None:
    """No password sources lists the discovered BIOS ChangePassword target."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.BiosChangePassword,
        "bios_change_password",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == {
        "bios": "/redfish/v1/Systems/System_0/Bios",
        "action": "#Bios.ChangePassword",
        "target": "/redfish/v1/Systems/System_0/Bios/Actions/Bios.ChangePassword",
    }
    assert _post_requests(service) == []


def test_bios_change_password_without_confirm_previews_and_redacts(
    redfish_mock_factory,
    monkeypatch,
) -> None:
    """Destructive password changes dry-run by default and mask payload secrets."""
    manager, service = redfish_mock_factory("supermicro")
    monkeypatch.setenv("OLD_BIOS_PASSWORD", "old-secret")
    monkeypatch.setenv("NEW_BIOS_PASSWORD", "new-secret")

    result = manager.sync_invoke(
        ApiRequestType.BiosChangePassword,
        "bios_change_password",
        password_name="Administrator",
        old_password_env="OLD_BIOS_PASSWORD",
        new_password_env="NEW_BIOS_PASSWORD",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] == "destructive action requires --confirm"
    assert result.data["action"] == "#Bios.ChangePassword"
    assert result.data["target"] == (
        "/redfish/v1/Systems/System_0/Bios/Actions/Bios.ChangePassword"
    )
    assert result.data["payload"] == {
        "PasswordName": "Administrator",
        "OldPassword": "********",
        "NewPassword": "********",
    }
    assert _post_requests(service) == []


def test_bios_change_password_confirm_posts_discovered_hpe_target(
    redfish_mock_factory,
    tmp_path,
) -> None:
    """--confirm POSTs to the vendor-discovered HPE ChangePassword target."""
    manager, service = redfish_mock_factory("hpe")
    old_file = tmp_path / "old-password"
    new_file = tmp_path / "new-password"
    old_file.write_text("old-secret\n", encoding="utf-8")
    new_file.write_text("new-secret\n", encoding="utf-8")

    result = manager.sync_invoke(
        ApiRequestType.BiosChangePassword,
        "bios_change_password",
        password_name="Administrator",
        old_password_file=str(old_file),
        new_password_file=str(new_file),
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#Bios.ChangePassword"
    assert result.data["target"] == (
        "/redfish/v1/systems/1/bios/Actions/Bios.ChangePasswords/"
    )
    assert result.data["level"] == "destructive"
    assert len(posts) == 1
    assert posts[0].path.lower() == (
        "/redfish/v1/systems/1/bios/actions/bios.changepasswords/"
    )
    assert posts[0].json() == {
        "PasswordName": "Administrator",
        "OldPassword": "old-secret",
        "NewPassword": "new-secret",
    }


def test_bios_change_password_confirm_dry_run_still_does_not_post(
    redfish_mock_factory,
    monkeypatch,
) -> None:
    """--dry_run wins over --confirm so operators can preview safely."""
    manager, service = redfish_mock_factory("supermicro")
    monkeypatch.setenv("OLD_BIOS_PASSWORD", "old-secret")
    monkeypatch.setenv("NEW_BIOS_PASSWORD", "new-secret")

    result = manager.sync_invoke(
        ApiRequestType.BiosChangePassword,
        "bios_change_password",
        password_name="User",
        old_password_env="OLD_BIOS_PASSWORD",
        new_password_env="NEW_BIOS_PASSWORD",
        confirm=True,
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] is None
    assert result.data["payload"]["OldPassword"] == "********"
    assert result.data["payload"]["NewPassword"] == "********"
    assert _post_requests(service) == []


def test_bios_change_password_missing_action_reports_available_without_post(
    redfish_mock_factory,
    monkeypatch,
) -> None:
    """A BIOS resource without ChangePassword returns an error and never POSTs."""
    manager, service = redfish_mock_factory("supermicro")
    monkeypatch.setenv("OLD_BIOS_PASSWORD", "old-secret")
    monkeypatch.setenv("NEW_BIOS_PASSWORD", "new-secret")
    bios_path = "/redfish/v1/Systems/System_0/Bios"
    bios = copy.deepcopy(service._state(bios_path))
    bios["Actions"].pop("#Bios.ChangePassword")
    service._overlay[bios_path] = bios
    service._overlay[bios_path.lower()] = bios

    result = manager.sync_invoke(
        ApiRequestType.BiosChangePassword,
        "bios_change_password",
        password_name="Administrator",
        old_password_env="OLD_BIOS_PASSWORD",
        new_password_env="NEW_BIOS_PASSWORD",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "action '#Bios.ChangePassword' not found on "
        "/redfish/v1/Systems/System_0/Bios"
    )
    assert result.data["action"] == "#Bios.ChangePassword"
    assert "ChangePassword" not in result.data["available"]
    assert "#Bios.ChangePassword" not in result.data["available"]
    assert _post_requests(service) == []


def test_bios_change_password_requires_complete_secret_sources(
    redfish_mock_factory,
    monkeypatch,
) -> None:
    """Supplying any change input requires password name plus old and new sources."""
    manager, service = redfish_mock_factory("supermicro")
    monkeypatch.setenv("OLD_BIOS_PASSWORD", "old-secret")

    with pytest.raises(
        InvalidArgument,
        match="new password source is required",
    ):
        manager.sync_invoke(
            ApiRequestType.BiosChangePassword,
            "bios_change_password",
            password_name="Administrator",
            old_password_env="OLD_BIOS_PASSWORD",
            confirm=True,
        )

    assert _post_requests(service) == []


def test_bios_change_password_rejects_missing_password_env(
    redfish_mock_factory,
    monkeypatch,
) -> None:
    """Missing password environment variables fail before any POST."""
    manager, service = redfish_mock_factory("supermicro")
    monkeypatch.setenv("NEW_BIOS_PASSWORD", "new-secret")

    with pytest.raises(
        InvalidArgument,
        match="old password environment variable 'MISSING_OLD_BIOS_PASSWORD'",
    ):
        manager.sync_invoke(
            ApiRequestType.BiosChangePassword,
            "bios_change_password",
            password_name="Administrator",
            old_password_env="MISSING_OLD_BIOS_PASSWORD",
            new_password_env="NEW_BIOS_PASSWORD",
            confirm=True,
        )

    assert _post_requests(service) == []


def test_bios_change_password_rejects_empty_password_name(
    redfish_mock_factory,
    monkeypatch,
) -> None:
    """PasswordName is explicit; no default is guessed for a lockout-sensitive action."""
    manager, service = redfish_mock_factory("supermicro")
    monkeypatch.setenv("OLD_BIOS_PASSWORD", "old-secret")
    monkeypatch.setenv("NEW_BIOS_PASSWORD", "new-secret")

    with pytest.raises(InvalidArgument, match="password name cannot be empty"):
        manager.sync_invoke(
            ApiRequestType.BiosChangePassword,
            "bios_change_password",
            password_name="  ",
            old_password_env="OLD_BIOS_PASSWORD",
            new_password_env="NEW_BIOS_PASSWORD",
            confirm=True,
        )

    assert _post_requests(service) == []


def test_bios_change_password_exposes_cli_entrypoint() -> None:
    """The bios-change-password command is wired into the package registry."""
    registry = BiosChangePassword.get_registry()

    assert registry[ApiRequestType.BiosChangePassword]["bios_change_password"] is (
        BiosChangePassword
    )
    _, cmd_name, cmd_help = BiosChangePassword.register_subcommand(BiosChangePassword)
    assert cmd_name == "bios-change-password"
    assert "password" in cmd_help
