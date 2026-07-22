"""Dual-mode-style tests for DellLCService.UpdateOSAppHealthData."""
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.actions.action_policy import Destructiveness, classify
from redfish_ctl.dell_lc.cmd_dell_lc_os_health_update import (
    DellLcOsHealthUpdate,
)
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

REPO_ROOT = Path(__file__).resolve().parents[1]
DELL_CORPUS = corpus_dir(
    REPO_ROOT / "tests" / "dell_xr8620t_corpus.tar.gz",
    "10.252.252.209",
)
DELL_LC_SERVICE = (
    "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellLCService"
)
TARGET_URI = f"{DELL_LC_SERVICE}/Actions/DellLCService.UpdateOSAppHealthData"


@pytest.fixture
def dell_lc_corpus_mock():
    """Return a manager and mock service backed by the full Dell corpus.

    :return: tuple of Redfish manager and mock service.
    """
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
                idrac_ip="mock-dell-lc-os-health",
                idrac_username="root",
                idrac_password="mock",
                insecure=True,
                is_debug=False,
            ),
            service,
        )


def _post_requests(service):
    """Return POST requests recorded by the mock Redfish service.

    :param service: mock Redfish service.
    :return: recorded POST requests.
    """
    return [request for request in service.requests if request.method == "POST"]


def test_dell_lc_os_health_update_policy_is_reversible():
    """The Dell LC OS-health refresh action is classified as reversible."""
    assert (
        classify("#DellLCService.UpdateOSAppHealthData")
        is Destructiveness.REVERSIBLE
    )


def test_dell_lc_os_health_update_lists_target_without_post(dell_lc_corpus_mock):
    """Listing discovers the action target and does not POST."""
    manager, service = dell_lc_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellLcOsHealthUpdate,
        "dell-lc-os-health-update",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    rows = result.data["os_health_update_targets"]
    assert rows == [{
        "Resource": DELL_LC_SERVICE,
        "Action": "#DellLCService.UpdateOSAppHealthData",
        "Target": TARGET_URI,
        "AllowedUpdateTypes": ["Automatic"],
    }]
    assert _post_requests(service) == []


def test_dell_lc_os_health_update_without_confirm_is_preview_only(
    dell_lc_corpus_mock,
):
    """An update type is resolved but not POSTed unless --confirm is present."""
    manager, service = dell_lc_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellLcOsHealthUpdate,
        "dell-lc-os-health-update",
        update_type="Automatic",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == "#DellLCService.UpdateOSAppHealthData"
    assert result.data["target"] == TARGET_URI
    assert result.data["level"] == "reversible"
    assert result.data["payload"] == {"UpdateType": "Automatic"}
    assert _post_requests(service) == []


def test_dell_lc_os_health_update_confirm_posts_payload(dell_lc_corpus_mock):
    """--confirm POSTs UpdateOSAppHealthData to the discovered action target."""
    manager, service = dell_lc_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellLcOsHealthUpdate,
        "dell-lc-os-health-update",
        update_type="Automatic",
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#DellLCService.UpdateOSAppHealthData"
    assert result.data["target"] == TARGET_URI
    assert result.data["level"] == "reversible"
    assert len(posts) == 1
    assert posts[0].path.lower() == TARGET_URI.lower()
    assert posts[0].json() == {"UpdateType": "Automatic"}


def test_dell_lc_os_health_update_dry_run_overrides_confirm(dell_lc_corpus_mock):
    """--dry_run keeps the command preview-only even with --confirm."""
    manager, service = dell_lc_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellLcOsHealthUpdate,
        "dell-lc-os-health-update",
        update_type="Automatic",
        dry_run=True,
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] is None
    assert result.data["payload"] == {"UpdateType": "Automatic"}
    assert _post_requests(service) == []


def test_dell_lc_os_health_update_rejects_invalid_update_type(
    dell_lc_corpus_mock,
):
    """Inline allowable values reject an unsupported UpdateType before POST."""
    manager, service = dell_lc_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellLcOsHealthUpdate,
        "dell-lc-os-health-update",
        update_type="Manual",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "invalid value for DellLCService.UpdateOSAppHealthData UpdateType: "
        "Manual; allowed: Automatic"
    )
    assert result.data["validation_errors"] == [{
        "parameter": "UpdateType",
        "value": "Manual",
        "allowed": ["Automatic"],
    }]
    assert _post_requests(service) == []


def test_dell_lc_os_health_update_missing_action_does_not_post(
    redfish_mock_factory,
):
    """A non-Dell fixture reports no OS-health target and no POST."""
    manager, service = redfish_mock_factory("generic")

    result = manager.sync_invoke(
        ApiRequestType.DellLcOsHealthUpdate,
        "dell-lc-os-health-update",
        update_type="Automatic",
    )

    assert isinstance(result, CommandResult)
    assert result.error == "Dell LC OS health update action not found"
    assert result.data == {
        "action": "#DellLCService.UpdateOSAppHealthData",
        "available": [],
    }
    assert _post_requests(service) == []


def test_dell_lc_os_health_update_exposes_cli_entrypoint():
    """The command is wired into the package registry."""
    registry = IDracManager().get_registry()
    assert (
        registry[ApiRequestType.DellLcOsHealthUpdate][
            "dell-lc-os-health-update"
        ]
        is DellLcOsHealthUpdate
    )

    cmd_parser, cmd_name, cmd_help = DellLcOsHealthUpdate.register_subcommand(
        DellLcOsHealthUpdate
    )

    assert "--update-type" in cmd_parser.format_help()
    assert cmd_name == "dell-lc-os-health-update"
    assert "OS application health" in cmd_help
