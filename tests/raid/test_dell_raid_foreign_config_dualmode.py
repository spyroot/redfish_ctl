"""Dual-mode-style coverage for DellRaidService foreign-config actions."""
import json
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.actions.action_policy import Destructiveness, classify
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.raid.cmd_dell_raid_foreign_config import DellRaidForeignConfigActions
from redfish_ctl.redfish_manager import CommandResult

REPO_ROOT = Path(__file__).resolve().parents[2]
DELL_CORPUS = corpus_dir(
    REPO_ROOT / "tests" / "dell_xr8620t_corpus.tar.gz",
    "10.252.252.209",
)
RAID_SERVICE = "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellRaidService"
IMPORT_TARGET = f"{RAID_SERVICE}/Actions/DellRaidService.ImportForeignConfig"
UNLOCK_TARGET = f"{RAID_SERVICE}/Actions/DellRaidService.UnLockSecureForeignConfig"


@pytest.fixture
def dell_corpus_mock():
    """Return a manager and mock service backed by the Dell XR8620t corpus."""
    requests_mock = pytest.importorskip("requests_mock")
    service = MockRedfishService(
        DELL_CORPUS, index=_build_fixture_index(DELL_CORPUS), vendor="dell")
    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=service.get_cb)
        mocker.patch(requests_mock.ANY, text=service.patch_cb)
        mocker.post(requests_mock.ANY, text=service.post_cb)
        mocker.delete(requests_mock.ANY, text=service.delete_cb)
        service.mocker = mocker
        yield (
            IDracManager(
                idrac_ip="mock-dell-raid-foreign-config",
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


def _without_action(action_name):
    """Return the DellRaidService fixture body with one action removed."""
    fixture = (
        DELL_CORPUS
        / "_redfish_v1_Systems_System.Embedded.1_Oem_Dell_DellRaidService.json"
    )
    body = json.loads(fixture.read_text())
    body["Actions"] = dict(body["Actions"])
    body["Actions"].pop(action_name, None)
    return body


def test_dell_raid_foreign_config_lists_targets_without_post(dell_corpus_mock):
    """Calling without an action lists advertised foreign-config actions."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidForeignConfigActions,
        "dell-raid-foreign-config",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["raid_service"] == RAID_SERVICE
    actions = {row["Action"]: row for row in result.data["actions"]}
    assert set(actions) == {"import", "unlock-secure"}
    assert actions["import"]["Target"] == IMPORT_TARGET
    assert actions["import"]["RequiredPayload"] == []
    assert actions["import"]["Level"] == "irreversible"
    assert actions["unlock-secure"]["Target"] == UNLOCK_TARGET
    assert _post_requests(service) == []


def test_dell_raid_import_foreign_config_previews_by_default(dell_corpus_mock):
    """ImportForeignConfig stays a no-POST preview by default."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidForeignConfigActions,
        "dell-raid-foreign-config",
        action="import",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == {
        "dry_run": True,
        "action": "#DellRaidService.ImportForeignConfig",
        "target": IMPORT_TARGET,
        "payload": {},
        "level": "irreversible",
        "blocked": (
            "irreversible action requires --confirm and "
            "--i-understand-irreversible"
        ),
    }
    assert _post_requests(service) == []


def test_dell_raid_foreign_config_confirm_alone_does_not_post(dell_corpus_mock):
    """Irreversible foreign-config actions require the extra confirmation token."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidForeignConfigActions,
        "dell-raid-foreign-config",
        action="unlock-secure",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["level"] == "irreversible"
    assert result.data["blocked"] == (
        "irreversible action requires --confirm and --i-understand-irreversible"
    )
    assert _post_requests(service) == []


def test_dell_raid_foreign_config_posts_with_both_confirmations(dell_corpus_mock):
    """Both confirmation flags POST to the corpus-advertised action target."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidForeignConfigActions,
        "dell-raid-foreign-config",
        action="unlock-secure",
        confirm=True,
        confirm_irreversible=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#DellRaidService.UnLockSecureForeignConfig"
    assert result.data["target"] == UNLOCK_TARGET
    assert result.data["task_id"] == MockRedfishService.JOB_ID
    assert len(posts) == 1
    assert posts[0].path.lower() == UNLOCK_TARGET.lower()
    assert posts[0].json() == {}


def test_dell_raid_foreign_config_dry_run_overrides_confirmation(dell_corpus_mock):
    """--dry_run keeps a fully confirmed foreign-config action from POSTing."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidForeignConfigActions,
        "dell-raid-foreign-config",
        action="import",
        confirm=True,
        confirm_irreversible=True,
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == {
        "dry_run": True,
        "action": "#DellRaidService.ImportForeignConfig",
        "target": IMPORT_TARGET,
        "payload": {},
        "level": "irreversible",
        "blocked": None,
    }
    assert _post_requests(service) == []


def test_dell_raid_foreign_config_missing_action_reports_available(dell_corpus_mock):
    """A service without the selected action returns an actionable no-POST error."""
    manager, service = dell_corpus_mock
    service._overlay[RAID_SERVICE.lower()] = _without_action(
        "#DellRaidService.ImportForeignConfig"
    )

    result = manager.sync_invoke(
        ApiRequestType.DellRaidForeignConfigActions,
        "dell-raid-foreign-config",
        action="import",
        confirm=True,
        confirm_irreversible=True,
    )

    assert isinstance(result, CommandResult)
    assert result.data["action"] == "#DellRaidService.ImportForeignConfig"
    assert result.error == (
        "action '#DellRaidService.ImportForeignConfig' not found on "
        f"{RAID_SERVICE}"
    )
    assert _post_requests(service) == []


def test_dell_raid_foreign_config_policy_and_registry_are_wired():
    """The command is registered and its foreign actions use the strongest guard."""
    registry = IDracManager().get_registry()
    assert registry[ApiRequestType.DellRaidForeignConfigActions][
        "dell-raid-foreign-config"
    ] is DellRaidForeignConfigActions

    for action in (
        "#DellRaidService.ImportForeignConfig",
        "#DellRaidService.UnLockSecureForeignConfig",
    ):
        assert classify(action) is Destructiveness.IRREVERSIBLE

    cmd_parser, cmd_name, cmd_help = (
        DellRaidForeignConfigActions.register_subcommand(
            DellRaidForeignConfigActions
        )
    )
    help_text = cmd_parser.format_help()
    assert cmd_name == "dell-raid-foreign-config"
    assert "foreign" in cmd_help
    assert "--action" in help_text
    assert "--confirm" in help_text
    assert "--i-understand-irreversible" in help_text
    assert "--dry_run" in help_text
