"""Dual-mode test for the read-only secure-boot command."""
import json

import pytest

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.security.cmd_secure_boot import SecureBootResetKeys

_SYSTEM_ID = "437XR1138R2"
_SECURE_BOOT_URI = f"/redfish/v1/Systems/{_SYSTEM_ID}/SecureBoot"
_SECURE_BOOT_RESET_TARGET = f"{_SECURE_BOOT_URI}/Actions/SecureBoot.ResetKeys"
_PK_URI = f"{_SECURE_BOOT_URI}/SecureBootDatabases/PK"
_PK_RESET_TARGET = f"{_PK_URI}/Actions/SecureBootDatabase.ResetKeys"


def _post_requests(service):
    """Return POST requests recorded by the mock Redfish service.

    :param service: mock Redfish service.
    :return: recorded POST requests.
    """
    return [request for request in service.requests if request.method == "POST"]


def test_secure_boot_returns_fixture_database_rows_without_mutation(
    monkeypatch,
    redfish_mock_factory,
):
    """secure-boot returns fixture database rows with GET-only traffic."""
    monkeypatch.delenv("IDRAC_IP", raising=False)
    redfish_api, redfish_service = redfish_mock_factory("hpe")

    result = redfish_api.sync_invoke(ApiRequestType.SecureBoot, "secure-boot")

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, list)
    assert result.data
    json.dumps(result.data, sort_keys=True)

    assert {
        row["DatabaseId"]
        for row in result.data
        if row["DatabaseId"] is not None
    } == {"PKDefault", "KEKDefault", "dbDefault", "dbxDefault"}
    assert {
        "System": "1",
        "SecureBootEnable": False,
        "SecureBootMode": "UserMode",
        "SecureBootCurrentBoot": "Disabled",
        "Database": "PKDefault",
        "DatabaseId": "PKDefault",
        "Certificates": 1,
    } in result.data
    assert redfish_service.requests
    assert all(
        request.method not in {"POST", "PATCH", "DELETE"}
        for request in redfish_service.requests
    )


def test_secure_boot_reset_keys_lists_generic_targets_without_post(
    redfish_mock_factory,
):
    """secure-boot-reset-keys lists system and database reset actions only."""
    redfish_api, redfish_service = redfish_mock_factory("generic")

    result = redfish_api.sync_invoke(
        ApiRequestType.SecureBootResetKeys,
        "secure-boot-reset-keys",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert _post_requests(redfish_service) == []
    targets = {row["Target"]: row for row in result.data}
    assert _SECURE_BOOT_RESET_TARGET in targets
    assert _PK_RESET_TARGET in targets
    assert targets[_SECURE_BOOT_RESET_TARGET]["ResetKeysType"] == [
        "ResetAllKeysToDefault",
        "DeleteAllKeys",
        "DeletePK",
    ]
    assert targets[_PK_RESET_TARGET]["Database"] == "PK"


def test_secure_boot_reset_keys_dry_runs_system_reset_without_post(
    redfish_mock_factory,
):
    """secure-boot-reset-keys previews SecureBoot.ResetKeys by default."""
    redfish_api, redfish_service = redfish_mock_factory("generic")

    result = redfish_api.sync_invoke(
        ApiRequestType.SecureBootResetKeys,
        "secure-boot-reset-keys",
        reset_type="ResetAllKeysToDefault",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == {
        "dry_run": True,
        "action": "#SecureBoot.ResetKeys",
        "target": _SECURE_BOOT_RESET_TARGET,
        "payload": {"ResetKeysType": "ResetAllKeysToDefault"},
        "level": "destructive",
        "blocked": "destructive action requires --confirm",
    }
    assert _post_requests(redfish_service) == []


def test_secure_boot_reset_keys_confirm_posts_database_reset(
    redfish_mock_factory,
):
    """--confirm posts SecureBootDatabase.ResetKeys to the selected database."""
    redfish_api, redfish_service = redfish_mock_factory("generic")

    result = redfish_api.sync_invoke(
        ApiRequestType.SecureBootResetKeys,
        "secure-boot-reset-keys",
        database="PK",
        reset_type="DeleteAllKeys",
        confirm=True,
    )

    posts = _post_requests(redfish_service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#SecureBootDatabase.ResetKeys"
    assert len(posts) == 1
    assert posts[0].path.lower() == _PK_RESET_TARGET.lower()
    assert posts[0].json() == {"ResetKeysType": "DeleteAllKeys"}


def test_secure_boot_reset_keys_rejects_unadvertised_reset_type_without_post(
    redfish_mock_factory,
):
    """ResetKeysType must be one of the values advertised by the target."""
    redfish_api, redfish_service = redfish_mock_factory("generic")

    with pytest.raises(InvalidArgument, match="ResetKeysType"):
        redfish_api.sync_invoke(
            ApiRequestType.SecureBootResetKeys,
            "secure-boot-reset-keys",
            database="PK",
            reset_type="DeletePK",
            confirm=True,
        )

    assert _post_requests(redfish_service) == []


def test_secure_boot_reset_keys_exposes_cli_entrypoint():
    """The guarded reset command is wired into the package registry."""
    registry = IDracManager().get_registry()
    assert registry[ApiRequestType.SecureBootResetKeys][
        "secure-boot-reset-keys"
    ] is SecureBootResetKeys

    cmd_parser, cmd_name, cmd_help = SecureBootResetKeys.register_subcommand(
        SecureBootResetKeys
    )

    help_text = cmd_parser.format_help()
    assert "--reset-type" in help_text
    assert "--confirm" in help_text
    assert cmd_name == "secure-boot-reset-keys"
    assert "SecureBoot" in cmd_help
