"""Dual-mode-style tests for Dell card-service key-management actions."""

from copy import deepcopy
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.actions.action_policy import classify
from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.oem.cmd_dell_card_key_management import DellCardKeyManagement
from redfish_ctl.redfish_manager import CommandResult

REPO_ROOT = Path(__file__).resolve().parents[2]
DELL_CORPUS = corpus_dir(
    REPO_ROOT / "tests" / "dell_xr8620t_corpus.tar.gz",
    "10.252.252.209",
)
GENERIC_MANAGER_URI = "/redfish/v1/Managers/BMC"
SERVICE_URI = "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DelliDRACCardService"
DISABLE_SEKM_TYPE = "#DelliDRACCardService.DisableSEKM"
DISABLE_SEKM_TARGET = (
    "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DelliDRACCardService/"
    "Actions/DelliDRACCardService.DisableSEKM"
)
REKEY_TYPE = "#DelliDRACCardService.Rekey"
REKEY_TARGET = (
    "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DelliDRACCardService/"
    "Actions/DelliDRACCardService.Rekey"
)


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
                idrac_ip="mock-dell-xr8620t",
                idrac_username="root",
                idrac_password="mock",
                insecure=True,
                is_debug=False,
            ),
            service,
        )


def _post_requests(redfish_service):
    """Return POST requests recorded by the mock Redfish service."""
    return [
        request
        for request in redfish_service.requests
        if request.method == "POST"
    ]


def _seed_card_service(redfish_service, include_action=True):
    """Overlay a Dell card-service link and optional key-management action."""
    manager = deepcopy(redfish_service._state(GENERIC_MANAGER_URI))
    manager.setdefault("Links", {}).setdefault("Oem", {}).setdefault("Dell", {})[
        "DelliDRACCardService"
    ] = {"@odata.id": SERVICE_URI}

    service = {
        "@odata.id": SERVICE_URI,
        "@odata.type": "#DelliDRACCardService.v1_0_0.DelliDRACCardService",
        "Id": "DelliDRACCardService",
        "Name": "Dell iDRAC Card Service",
        "Actions": {},
    }
    if include_action:
        service["Actions"][DISABLE_SEKM_TYPE] = {"target": DISABLE_SEKM_TARGET}

    for path, body in (
        (GENERIC_MANAGER_URI, manager),
        (SERVICE_URI, service),
    ):
        redfish_service._overlay[path] = body
        redfish_service._overlay[path.lower()] = body


def test_dell_card_key_management_lists_corpus_targets(dell_corpus_mock):
    """Listing discovers Dell corpus key-management action targets."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellCardKeyManagement,
        "dell-card-key-management",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    targets = {row["Action"]: row for row in result.data["key_management_targets"]}
    assert set(targets) == {
        "disable-ilkm",
        "disable-sekm",
        "enable-ilkm",
        "enable-sekm",
        "rekey",
        "transition-ilkm-to-sekm",
    }
    assert targets["disable-sekm"]["Target"] == DISABLE_SEKM_TARGET
    assert targets["rekey"]["Target"] == REKEY_TARGET
    assert targets["rekey"]["Parameters"] == {"Mode": ["SEKM", "iLKM"]}
    assert _post_requests(service) == []


def test_dell_card_key_management_previews_by_default(dell_corpus_mock):
    """A selected key-management action resolves but does not POST by default."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellCardKeyManagement,
        "dell-card-key-management",
        action="disable-sekm",
    )

    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == DISABLE_SEKM_TYPE
    assert result.data["target"] == DISABLE_SEKM_TARGET
    assert result.data["payload"] == {}
    assert result.data["level"] == "destructive"
    assert result.data["blocked"] == "destructive action requires --confirm"
    assert _post_requests(service) == []


def test_dell_card_key_management_confirm_posts_rekey_payload(dell_corpus_mock):
    """--confirm POSTs exactly one Dell card-service Rekey request."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellCardKeyManagement,
        "dell-card-key-management",
        action="rekey",
        mode="SEKM",
        confirm=True,
    )

    posts = _post_requests(service)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == REKEY_TYPE
    assert result.data["level"] == "destructive"
    assert len(posts) == 1
    assert posts[0].path.lower() == REKEY_TARGET.lower()
    assert posts[0].json() == {"Mode": "SEKM"}


def test_dell_card_key_management_rejects_invalid_rekey_mode(dell_corpus_mock):
    """Inline Redfish metadata rejects unsupported Rekey Mode values."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellCardKeyManagement,
        "dell-card-key-management",
        action="rekey",
        mode="BadMode",
        confirm=True,
    )

    assert result.error == (
        "invalid value for DelliDRACCardService.Rekey Mode: "
        "BadMode; allowed: SEKM, iLKM"
    )
    assert result.data["validation_errors"] == [{
        "parameter": "Mode",
        "value": "BadMode",
        "allowed": ["SEKM", "iLKM"],
    }]
    assert _post_requests(service) == []


def test_dell_card_key_management_requires_mode_only_for_rekey(dell_corpus_mock):
    """Mode is required for Rekey and rejected for other selectors."""
    manager, service = dell_corpus_mock

    with pytest.raises(InvalidArgument, match="--mode is required"):
        manager.sync_invoke(
            ApiRequestType.DellCardKeyManagement,
            "dell-card-key-management",
            action="rekey",
            confirm=True,
        )
    with pytest.raises(InvalidArgument, match="only valid with --action rekey"):
        manager.sync_invoke(
            ApiRequestType.DellCardKeyManagement,
            "dell-card-key-management",
            action="enable-sekm",
            mode="SEKM",
        )

    assert _post_requests(service) == []


def test_dell_card_key_management_missing_action_reports_without_post(
    redfish_mock,
    redfish_service,
):
    """A card-service resource without the selected action raises before POST."""
    _seed_card_service(redfish_service, include_action=False)

    with pytest.raises(InvalidArgument, match="action not found: disable-sekm"):
        redfish_mock.sync_invoke(
            ApiRequestType.DellCardKeyManagement,
            "dell-card-key-management",
            action="disable-sekm",
            confirm=True,
        )

    assert _post_requests(redfish_service) == []


def test_dell_card_key_management_exposes_policy_and_cli_entrypoint():
    """The dell-card-key-management command is wired into policy and registry."""
    assert classify(DISABLE_SEKM_TYPE).value == "destructive"
    assert classify(REKEY_TYPE).value == "destructive"

    registry = IDracManager().get_registry()
    assert registry[ApiRequestType.DellCardKeyManagement][
        "dell-card-key-management"
    ] is DellCardKeyManagement

    cmd_parser, cmd_name, cmd_help = DellCardKeyManagement.register_subcommand(
        DellCardKeyManagement
    )
    help_text = cmd_parser.format_help()

    assert "--action" in help_text
    assert "--mode" in help_text
    assert "--resource-uri" in help_text
    assert "--confirm" in help_text
    assert "--dry_run" in help_text
    assert cmd_name == "dell-card-key-management"
    assert "key-management" in cmd_help
