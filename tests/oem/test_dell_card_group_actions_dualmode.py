"""Dual-mode tests for Dell card-service group actions."""
import json

import pytest
from conftest import MockRedfishService, _build_fixture_index

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.oem.cmd_dell_card_group_actions import DellCardGroupActions
from redfish_ctl.redfish_manager import CommandResult

SERVICE_URI = "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DelliDRACCardService"
JOIN_TARGET = f"{SERVICE_URI}/Actions/DelliDRACCardService.JoinGroup"
DELETE_TARGET = f"{SERVICE_URI}/Actions/DelliDRACCardService.DeleteGroup"
REMOVE_TARGET = f"{SERVICE_URI}/Actions/DelliDRACCardService.RemoveSelf"


def _write_fixture(root, name, data):
    """Write a JSON fixture file under the temporary Redfish mock tree."""
    (root / name).write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def dell_card_group_mock(tmp_path):
    """Return a manager and mock service with DelliDRACCardService fixtures."""
    requests_mock = pytest.importorskip("requests_mock")
    _write_fixture(
        tmp_path,
        "_redfish_v1_Managers.json",
        {
            "@odata.id": "/redfish/v1/Managers",
            "Members": [
                {"@odata.id": "/redfish/v1/Managers/iDRAC.Embedded.1"},
            ],
            "Members@odata.count": 1,
        },
    )
    _write_fixture(
        tmp_path,
        "_redfish_v1_Managers_iDRAC.Embedded.1.json",
        {
            "@odata.id": "/redfish/v1/Managers/iDRAC.Embedded.1",
            "Id": "iDRAC.Embedded.1",
            "Links": {
                "Oem": {
                    "Dell": {
                        "DelliDRACCardService": {
                            "@odata.id": SERVICE_URI,
                        },
                    },
                },
            },
        },
    )
    _write_fixture(
        tmp_path,
        "_redfish_v1_Managers_iDRAC.Embedded.1_Oem_Dell_"
        "DelliDRACCardService.json",
        {
            "@odata.id": SERVICE_URI,
            "@odata.type": "#DelliDRACCardService.v1_6_0.DelliDRACCardService",
            "Id": "DelliDRACCardService",
            "Actions": {
                "#DelliDRACCardService.DeleteGroup": {
                    "target": DELETE_TARGET,
                },
                "#DelliDRACCardService.JoinGroup": {
                    "CloneConfiguration@Redfish.AllowableValues": [
                        "Disable",
                        "Enable",
                    ],
                    "target": JOIN_TARGET,
                },
                "#DelliDRACCardService.RemoveSelf": {
                    "target": REMOVE_TARGET,
                },
            },
        },
    )
    service = MockRedfishService(tmp_path, index=_build_fixture_index(tmp_path))
    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=service.get_cb)
        mocker.patch(requests_mock.ANY, text=service.patch_cb)
        mocker.post(requests_mock.ANY, text=service.post_cb)
        mocker.delete(requests_mock.ANY, text=service.delete_cb)
        service.mocker = mocker
        yield (
            IDracManager(
                idrac_ip="mock-dell-card",
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


def test_dell_card_group_actions_list_targets_without_post(dell_card_group_mock):
    """Listing discovers Dell card group actions without POSTing."""
    manager, service = dell_card_group_mock

    result = manager.sync_invoke(
        ApiRequestType.DellCardGroupActions,
        "dell-card-group-actions",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    rows = result.data["group_action_targets"]
    actions = {row["Action"]: row for row in rows}
    assert set(actions) == {"delete-group", "join", "remove-self"}
    assert actions["join"]["Target"] == JOIN_TARGET
    assert actions["join"]["Level"] == "destructive"
    assert actions["join"]["Parameters"]["CloneConfiguration"] == [
        "Disable",
        "Enable",
    ]
    assert actions["remove-self"]["Target"] == REMOVE_TARGET
    assert _post_requests(service) == []


def test_dell_card_group_join_previews_by_default(dell_card_group_mock):
    """A selected group action resolves the target but does not POST by default."""
    manager, service = dell_card_group_mock

    result = manager.sync_invoke(
        ApiRequestType.DellCardGroupActions,
        "dell-card-group-actions",
        action="join",
        clone_configuration="Enable",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == "#DelliDRACCardService.JoinGroup"
    assert result.data["level"] == "destructive"
    assert result.data["blocked"] == "destructive action requires --confirm"
    assert result.data["target"] == JOIN_TARGET
    assert result.data["payload"] == {"CloneConfiguration": "Enable"}
    assert _post_requests(service) == []


def test_dell_card_group_remove_self_confirm_posts(dell_card_group_mock):
    """--confirm posts exactly one selected Dell card group action."""
    manager, service = dell_card_group_mock

    result = manager.sync_invoke(
        ApiRequestType.DellCardGroupActions,
        "dell-card-group-actions",
        action="remove-self",
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#DelliDRACCardService.RemoveSelf"
    assert result.data["level"] == "destructive"
    assert len(posts) == 1
    assert posts[0].path.lower() == REMOVE_TARGET.lower()
    assert posts[0].json() == {}


def test_dell_card_group_rejects_clone_configuration_on_non_join(
    dell_card_group_mock,
):
    """CloneConfiguration is only valid for JoinGroup."""
    manager, service = dell_card_group_mock

    with pytest.raises(InvalidArgument, match="only valid with --action join"):
        manager.sync_invoke(
            ApiRequestType.DellCardGroupActions,
            "dell-card-group-actions",
            action="delete-group",
            clone_configuration="Enable",
            confirm=True,
        )

    assert _post_requests(service) == []


def test_dell_card_group_invalid_clone_value_reports_without_post(
    dell_card_group_mock,
):
    """JoinGroup payload validation uses the corpus-advertised allowable values."""
    manager, service = dell_card_group_mock

    result = manager.sync_invoke(
        ApiRequestType.DellCardGroupActions,
        "dell-card-group-actions",
        action="join",
        clone_configuration="Maybe",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error
    assert "invalid value for DelliDRACCardService.JoinGroup" in result.error
    assert result.data["validation_errors"][0]["parameter"] == "CloneConfiguration"
    assert _post_requests(service) == []


def test_dell_card_group_actions_exposes_cli_entrypoint():
    """The dell-card-group-actions command is wired into the package registry."""
    registry = IDracManager().get_registry()
    assert registry[ApiRequestType.DellCardGroupActions][
        "dell-card-group-actions"
    ] is DellCardGroupActions

    cmd_parser, cmd_name, cmd_help = DellCardGroupActions.register_subcommand(
        DellCardGroupActions
    )

    assert "--action" in cmd_parser.format_help()
    assert "--clone-configuration" in cmd_parser.format_help()
    assert cmd_name == "dell-card-group-actions"
    assert "Dell card-service group" in cmd_help
