"""Dual-mode-style coverage for DellRaidService clear actions."""
import json
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.actions.action_policy import Destructiveness, classify
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.raid.cmd_dell_raid_clear_actions import DellRaidClearActions
from redfish_ctl.redfish_manager import CommandResult

REPO_ROOT = Path(__file__).resolve().parents[1]
DELL_CORPUS = corpus_dir(
    REPO_ROOT / "tests" / "dell_xr8620t_corpus.tar.gz",
    "10.252.252.209",
)
RAID_SERVICE = "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellRaidService"
FOREIGN_TARGET = f"{RAID_SERVICE}/Actions/DellRaidService.ClearForeignConfig"
PRESERVED_TARGET = (
    f"{RAID_SERVICE}/Actions/DellRaidService.ClearControllerPreservedCache"
)


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
                idrac_ip="mock-dell-raid-clear-actions",
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


def test_dell_raid_clear_actions_list_targets_without_post(dell_corpus_mock):
    """Calling without an action lists advertised clear actions."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidClearActions,
        "dell-raid-clear-actions",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["raid_service"] == RAID_SERVICE
    actions = {row["Action"]: row for row in result.data["actions"]}
    assert set(actions) == {"foreign-config", "preserved-cache"}
    assert actions["foreign-config"]["Target"] == FOREIGN_TARGET
    assert actions["foreign-config"]["RequiredPayload"] == []
    assert actions["foreign-config"]["Level"] == "irreversible"
    assert actions["preserved-cache"]["Target"] == PRESERVED_TARGET
    assert _post_requests(service) == []


def test_dell_raid_clear_foreign_config_previews_by_default(dell_corpus_mock):
    """ClearForeignConfig stays a no-POST preview by default."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidClearActions,
        "dell-raid-clear-actions",
        action="foreign-config",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == {
        "dry_run": True,
        "action": "#DellRaidService.ClearForeignConfig",
        "target": FOREIGN_TARGET,
        "payload": {},
        "level": "irreversible",
        "blocked": (
            "irreversible action requires --confirm and "
            "--i-understand-irreversible"
        ),
    }
    assert _post_requests(service) == []


def test_dell_raid_clear_confirm_alone_does_not_post(dell_corpus_mock):
    """Irreversible clear actions require the extra confirmation token."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidClearActions,
        "dell-raid-clear-actions",
        action="preserved-cache",
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


def test_dell_raid_clear_posts_with_both_confirmations(dell_corpus_mock):
    """Both confirmation flags POST to the corpus-advertised action target."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidClearActions,
        "dell-raid-clear-actions",
        action="foreign-config",
        confirm=True,
        confirm_irreversible=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#DellRaidService.ClearForeignConfig"
    assert result.data["target"] == FOREIGN_TARGET
    assert result.data["task_id"] == MockRedfishService.JOB_ID
    assert len(posts) == 1
    assert posts[0].path.lower() == FOREIGN_TARGET.lower()
    assert posts[0].json() == {}


def test_dell_raid_clear_dry_run_overrides_confirmation(dell_corpus_mock):
    """--dry_run keeps a fully confirmed clear action from POSTing."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidClearActions,
        "dell-raid-clear-actions",
        action="preserved-cache",
        confirm=True,
        confirm_irreversible=True,
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == {
        "dry_run": True,
        "action": "#DellRaidService.ClearControllerPreservedCache",
        "target": PRESERVED_TARGET,
        "payload": {},
        "level": "irreversible",
        "blocked": None,
    }
    assert _post_requests(service) == []


def test_dell_raid_clear_missing_action_reports_available(dell_corpus_mock):
    """A service without the selected action returns an actionable no-POST error."""
    manager, service = dell_corpus_mock
    service._overlay[RAID_SERVICE.lower()] = _without_action(
        "#DellRaidService.ClearForeignConfig"
    )

    result = manager.sync_invoke(
        ApiRequestType.DellRaidClearActions,
        "dell-raid-clear-actions",
        action="foreign-config",
        confirm=True,
        confirm_irreversible=True,
    )

    assert isinstance(result, CommandResult)
    assert result.data["action"] == "#DellRaidService.ClearForeignConfig"
    assert result.error == (
        "action '#DellRaidService.ClearForeignConfig' not found on "
        f"{RAID_SERVICE}"
    )
    assert _post_requests(service) == []


def test_dell_raid_clear_policy_and_registry_are_wired():
    """The command is registered and its clear actions use the strongest guard."""
    registry = IDracManager().get_registry()
    assert registry[ApiRequestType.DellRaidClearActions][
        "dell-raid-clear-actions"
    ] is DellRaidClearActions

    for action in (
        "#DellRaidService.ClearControllerPreservedCache",
        "#DellRaidService.ClearForeignConfig",
    ):
        assert classify(action) is Destructiveness.IRREVERSIBLE

    cmd_parser, cmd_name, cmd_help = DellRaidClearActions.register_subcommand(
        DellRaidClearActions
    )
    help_text = cmd_parser.format_help()
    assert cmd_name == "dell-raid-clear-actions"
    assert "clear actions" in cmd_help
    assert "--action" in help_text
    assert "--confirm" in help_text
    assert "--i-understand-irreversible" in help_text
    assert "--dry_run" in help_text
