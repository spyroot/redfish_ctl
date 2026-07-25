"""Dual-mode-style tests for the Dell SEKM server connectivity test command."""

from copy import deepcopy
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.oem.cmd_dell_card_sekm_test import DellCardSekmTest
from redfish_ctl.redfish_manager import CommandResult

REPO_ROOT = Path(__file__).resolve().parents[2]
DELL_CORPUS = corpus_dir(
    REPO_ROOT / "tests" / "dell_xr8620t_corpus.tar.gz",
    "10.252.252.209",
)
GENERIC_MANAGER_URI = "/redfish/v1/Managers/BMC"
SERVICE_URI = "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DelliDRACCardService"
TARGET_URI = (
    "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DelliDRACCardService/"
    "Actions/DelliDRACCardService.TestSEKMServerConnection"
)
ACTION_TYPE = "#DelliDRACCardService.TestSEKMServerConnection"


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
    """Overlay a Dell card service link and optional SEKM test action."""
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
        service["Actions"][ACTION_TYPE] = {
            "target": TARGET_URI,
            "ServerType@Redfish.AllowableValues": ["Primary", "Secondary"],
        }

    for path, body in (
        (GENERIC_MANAGER_URI, manager),
        (SERVICE_URI, service),
    ):
        redfish_service._overlay[path] = body
        redfish_service._overlay[path.lower()] = body


def test_dell_card_sekm_test_lists_corpus_target(dell_corpus_mock):
    """Listing discovers the Dell corpus card-service SEKM test action."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellCardSekmTest,
        "dell-card-sekm-test",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["sekm_test_targets"] == [{
        "Resource": SERVICE_URI,
        "Action": ACTION_TYPE,
        "Target": TARGET_URI,
        "AllowedServerTypes": ["Primary", "Secondary"],
    }]
    assert _post_requests(service) == []


def test_dell_card_sekm_test_previews_primary_by_default(
    dell_corpus_mock,
):
    """A selected SEKM test previews unless --confirm is supplied."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellCardSekmTest,
        "dell-card-sekm-test",
        server_type="Primary",
    )

    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == ACTION_TYPE
    assert result.data["level"] == "reversible"
    assert result.data["target"] == TARGET_URI
    assert result.data["payload"] == {"ServerType": "Primary"}
    assert result.data["requires_confirm"] is True
    assert result.data["blocked"] == "Dell SEKM server test requires --confirm"
    assert _post_requests(service) == []


def test_dell_card_sekm_test_confirm_posts_secondary(
    dell_corpus_mock,
):
    """--confirm POSTs exactly one selected Dell SEKM server test."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellCardSekmTest,
        "dell-card-sekm-test",
        server_type="Secondary",
        confirm=True,
    )

    posts = _post_requests(service)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == ACTION_TYPE
    assert result.data["level"] == "reversible"
    assert len(posts) == 1
    assert posts[0].path.lower() == TARGET_URI.lower()
    assert posts[0].json() == {"ServerType": "Secondary"}


def test_dell_card_sekm_test_dry_run_overrides_confirm(
    dell_corpus_mock,
):
    """--dry_run remains a preview even when --confirm is also passed."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellCardSekmTest,
        "dell-card-sekm-test",
        server_type="Primary",
        confirm=True,
        dry_run=True,
    )

    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] is None
    assert result.data["payload"] == {"ServerType": "Primary"}
    assert _post_requests(service) == []


def test_dell_card_sekm_test_rejects_invalid_server_type(
    dell_corpus_mock,
):
    """Inline Redfish metadata rejects unsupported ServerType values."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellCardSekmTest,
        "dell-card-sekm-test",
        server_type="Tertiary",
        confirm=True,
    )

    assert result.error == (
        "invalid value for DelliDRACCardService.TestSEKMServerConnection "
        "ServerType: Tertiary; allowed: Primary, Secondary"
    )
    assert result.data["validation_errors"] == [{
        "parameter": "ServerType",
        "value": "Tertiary",
        "allowed": ["Primary", "Secondary"],
    }]
    assert _post_requests(service) == []


def test_dell_card_sekm_test_missing_action_reports_without_post(
    redfish_mock,
    redfish_service,
):
    """A card-service resource without the SEKM action returns a target error."""
    _seed_card_service(redfish_service, include_action=False)

    result = redfish_mock.sync_invoke(
        ApiRequestType.DellCardSekmTest,
        "dell-card-sekm-test",
        server_type="Primary",
        confirm=True,
    )

    assert result.error == "Dell card SEKM test action not found"
    assert result.data == {"action": ACTION_TYPE, "available": []}
    assert _post_requests(redfish_service) == []


def test_dell_card_sekm_test_exposes_cli_entrypoint():
    """The dell-card-sekm-test command is wired into the package registry."""
    registry = IDracManager().get_registry()
    assert registry[ApiRequestType.DellCardSekmTest]["dell-card-sekm-test"] is (
        DellCardSekmTest
    )

    cmd_parser, cmd_name, cmd_help = DellCardSekmTest.register_subcommand(
        DellCardSekmTest
    )
    help_text = cmd_parser.format_help()

    assert "--server-type" in help_text
    assert "--resource-uri" in help_text
    assert "--confirm" in help_text
    assert "--dry_run" in help_text
    assert cmd_name == "dell-card-sekm-test"
    assert "SEKM" in cmd_help
