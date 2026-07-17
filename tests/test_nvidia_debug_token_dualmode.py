"""Dual-mode tests for NVIDIA debug-token actions."""

import pytest

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.oem.cmd_nvidia_debug_token import NvidiaDebugToken
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_manager_shared import ApiRequestType

_TOKEN_RESOURCE = "/redfish/v1/Systems/HGX_Baseboard_0/Oem/Nvidia/CPUDebugToken"
_GENERATE_TARGET = (
    "/redfish/v1/Systems/HGX_Baseboard_0/Oem/Nvidia/CPUDebugToken/Actions/"
    "NvidiaDebugToken.GenerateToken"
)
_DISABLE_TARGET = (
    "/redfish/v1/Systems/HGX_Baseboard_0/Oem/Nvidia/CPUDebugToken/Actions/"
    "NvidiaDebugToken.DisableToken"
)
_INSTALL_TARGET = (
    "/redfish/v1/Systems/HGX_Baseboard_0/Oem/Nvidia/CPUDebugToken/Actions/"
    "NvidiaDebugToken.InstallToken"
)


def _post_requests(service):
    """Return POST requests recorded by the mock Redfish service."""
    return [request for request in service.requests if request.method == "POST"]


def test_nvidia_debug_token_lists_fixture_targets(redfish_mock_factory):
    """Listing discovers the GB300 NVIDIA CPUDebugToken action targets."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.NvidiaDebugToken,
        "nvidia-debug-token",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    targets = result.data["debug_token_targets"]
    assert len(targets) == 1
    row = targets[0]
    assert row["System"] == "HGX_Baseboard_0"
    assert row["Uri"] == _TOKEN_RESOURCE
    assert row["Status"] == "NoTokenApplied"
    actions = {action["Action"]: action for action in row["Actions"]}
    assert set(actions) == {"disable", "generate", "install"}
    assert actions["generate"]["Target"] == _GENERATE_TARGET
    assert actions["install"]["Target"] == _INSTALL_TARGET
    assert _post_requests(service) == []


def test_nvidia_debug_token_previews_generate_by_default(redfish_mock_factory):
    """A selected generate action previews unless --confirm is given."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.NvidiaDebugToken,
        "nvidia-debug-token",
        action="generate",
        token_type="CRCS",
    )

    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == "#NvidiaDebugToken.GenerateToken"
    assert result.data["level"] == "reversible"
    assert result.data["target"] == _GENERATE_TARGET
    assert result.data["payload"] == {"TokenType": "CRCS"}
    assert result.data["blocked"] is None
    assert _post_requests(service) == []


def test_nvidia_debug_token_confirm_posts_disable(redfish_mock_factory):
    """--confirm posts exactly one selected reversible debug-token action."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.NvidiaDebugToken,
        "nvidia-debug-token",
        action="disable",
        system="HGX_Baseboard_0",
        confirm=True,
    )

    posts = _post_requests(service)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#NvidiaDebugToken.DisableToken"
    assert result.data["level"] == "reversible"
    assert len(posts) == 1
    assert posts[0].path.lower() == _DISABLE_TARGET.lower()
    assert posts[0].json() == {}


def test_nvidia_debug_token_install_requires_token_source(redfish_mock_factory):
    """Install refuses to build a payload without env/file token material."""
    manager, service = redfish_mock_factory("supermicro")

    with pytest.raises(InvalidArgument, match="requires --token-env or --token-file"):
        manager.sync_invoke(
            ApiRequestType.NvidiaDebugToken,
            "nvidia-debug-token",
            action="install",
            confirm=True,
        )

    assert _post_requests(service) == []


def test_nvidia_debug_token_install_preview_redacts_token(
    redfish_mock_factory,
    monkeypatch,
):
    """Install previews by default and masks token material in returned data."""
    monkeypatch.setenv("DEBUG_TOKEN_VALUE", "sensitive-token")
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.NvidiaDebugToken,
        "nvidia-debug-token",
        action="install",
        token_env="DEBUG_TOKEN_VALUE",
        token_field="DebugToken",
    )

    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == "#NvidiaDebugToken.InstallToken"
    assert result.data["level"] == "destructive"
    assert result.data["target"] == _INSTALL_TARGET
    assert result.data["payload"] == {"DebugToken": "********"}
    assert result.data["blocked"] == "destructive action requires --confirm"
    assert _post_requests(service) == []


def test_nvidia_debug_token_install_confirm_posts_unredacted_token(
    redfish_mock_factory,
    monkeypatch,
):
    """--confirm sends the token payload while returned data stays redacted."""
    monkeypatch.setenv("DEBUG_TOKEN_VALUE", "sensitive-token")
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.NvidiaDebugToken,
        "nvidia-debug-token",
        action="install",
        token_env="DEBUG_TOKEN_VALUE",
        confirm=True,
    )

    posts = _post_requests(service)
    assert result.error is None
    assert result.data["executed"] is True
    assert "payload" not in result.data
    assert len(posts) == 1
    assert posts[0].path.lower() == _INSTALL_TARGET.lower()
    assert posts[0].json() == {"Token": "sensitive-token"}


def test_nvidia_debug_token_missing_target_reports_without_post(
    redfish_mock_factory,
):
    """A fixture without NVIDIA debug-token resources reports a selector error."""
    manager, service = redfish_mock_factory("generic")

    with pytest.raises(InvalidArgument, match="debug-token resource not found"):
        manager.sync_invoke(
            ApiRequestType.NvidiaDebugToken,
            "nvidia-debug-token",
            action="generate",
            confirm=True,
        )

    assert _post_requests(service) == []


def test_nvidia_debug_token_exposes_cli_entrypoint():
    """The nvidia-debug-token command is wired into the package registry."""
    registry = RedfishManagerBase().get_registry()
    assert registry[ApiRequestType.NvidiaDebugToken]["nvidia-debug-token"] is (
        NvidiaDebugToken
    )

    cmd_parser, cmd_name, cmd_help = NvidiaDebugToken.register_subcommand(
        NvidiaDebugToken
    )

    assert "--action" in cmd_parser.format_help()
    assert "--token-env" in cmd_parser.format_help()
    assert cmd_name == "nvidia-debug-token"
    assert "NVIDIA" in cmd_help
