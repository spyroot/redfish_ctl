"""Dual-mode-style coverage for DellLCService.TestNetworkShare."""
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.dell_lc.cmd_dell_lc_network_share_test import (
    DellLcNetworkShareTest,
)
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

DELL_CORPUS = corpus_dir(
    Path(__file__).parent.parent / "dell_xr8620t_corpus.tar.gz",
    "10.252.252.209",
)
SERVICE_URI = "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellLCService"
TARGET_URI = f"{SERVICE_URI}/Actions/DellLCService.TestNetworkShare"


@pytest.fixture
def dell_lc_mock():
    """Return a manager and mock service backed by the Dell XR8620t corpus.

    TestNetworkShare is a documented-sync action: the vendor-faithful service
    answers its POST with 200 plus a Base success message and never a task
    (see ``_SYNC_ACTION_SUFFIXES`` in conftest for the Dell evidence).

    :return: tuple of IDracManager and the recording MockRedfishService.
    """
    requests_mock = pytest.importorskip("requests_mock")
    service = MockRedfishService(
        DELL_CORPUS,
        index=_build_fixture_index(DELL_CORPUS),
        vendor="dell",
    )
    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=service.get_cb)
        mocker.patch(requests_mock.ANY, text=service.patch_cb)
        mocker.post(requests_mock.ANY, text=service.post_cb)
        mocker.delete(requests_mock.ANY, text=service.delete_cb)
        service.mocker = mocker
        yield (
            IDracManager(
                idrac_ip="mock-dell-lc",
                idrac_username="root",
                idrac_password="mock",
                insecure=True,
                is_debug=False,
            ),
            service,
        )


def _post_requests(service):
    """Return POST requests recorded by the mock Redfish service.

    :param service: the recording MockRedfishService.
    :return: list of POST requests.
    """
    return [request for request in service.requests if request.method == "POST"]


def test_dell_lc_network_share_test_lists_target_without_mutating(
    dell_lc_mock,
):
    """Without --host, the command lists the discovered action target only."""
    manager, service = dell_lc_mock

    result = manager.sync_invoke(
        ApiRequestType.DellLcNetworkShareTest,
        "dell-lc-network-share-test",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["service"] == SERVICE_URI
    assert result.data["action"] == "#DellLCService.TestNetworkShare"
    assert result.data["target"] == TARGET_URI
    assert result.data["allowable_values"]["ShareType"] == [
        "CIFS",
        "FTP",
        "HTTP",
        "HTTPS",
        "NFS",
        "TFTP",
    ]
    assert _post_requests(service) == []


def test_dell_lc_network_share_test_without_confirm_is_preview_only(
    dell_lc_mock,
):
    """A host payload is resolved but not POSTed unless --confirm is present."""
    manager, service = dell_lc_mock

    result = manager.sync_invoke(
        ApiRequestType.DellLcNetworkShareTest,
        "dell-lc-network-share-test",
        host="repo.example.test",
        share_type="HTTPS",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == "#DellLCService.TestNetworkShare"
    assert result.data["target"] == TARGET_URI
    assert result.data["level"] == "reversible"
    assert result.data["payload"] == {
        "IPAddress": "repo.example.test",
        "ShareType": "HTTPS",
        "ProxySupport": "Off",
        "IgnoreCertWarning": "On",
    }
    assert _post_requests(service) == []


def test_dell_lc_network_share_test_confirm_posts_payload(dell_lc_mock):
    """--confirm POSTs TestNetworkShare; the action realizes synchronously.

    The Dell-faithful answer is a terminal 200 success, so the result must
    carry no fabricated ``task_id`` (the realization is sync, not a job).
    """
    manager, service = dell_lc_mock

    result = manager.sync_invoke(
        ApiRequestType.DellLcNetworkShareTest,
        "dell-lc-network-share-test",
        host="repo.example.test",
        share_type="HTTPS",
        proxy_support="ParametersProxy",
        ignore_cert_warning="Off",
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#DellLCService.TestNetworkShare"
    assert result.data["target"] == TARGET_URI
    assert result.data["level"] == "reversible"
    assert "task_id" not in result.data
    assert len(posts) == 1
    assert posts[0].path.lower() == TARGET_URI.lower()
    assert posts[0].json() == {
        "IPAddress": "repo.example.test",
        "ShareType": "HTTPS",
        "ProxySupport": "ParametersProxy",
        "IgnoreCertWarning": "Off",
    }


def test_dell_lc_network_share_test_rejects_invalid_share_type(
    dell_lc_mock,
):
    """Inline allowable values reject an unsupported ShareType before POST."""
    manager, service = dell_lc_mock

    result = manager.sync_invoke(
        ApiRequestType.DellLcNetworkShareTest,
        "dell-lc-network-share-test",
        host="repo.example.test",
        share_type="Local",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "invalid value for DellLCService.TestNetworkShare ShareType: "
        "Local; allowed: CIFS, FTP, HTTP, HTTPS, NFS, TFTP"
    )
    assert result.data["validation_errors"] == [{
        "parameter": "ShareType",
        "value": "Local",
        "allowed": ["CIFS", "FTP", "HTTP", "HTTPS", "NFS", "TFTP"],
    }]
    assert _post_requests(service) == []


def test_dell_lc_network_share_test_requires_nonempty_host():
    """Payload construction rejects an empty network share host."""
    with pytest.raises(InvalidArgument, match="network share host cannot be empty"):
        DellLcNetworkShareTest._payload("  ", "HTTPS", "Off", "On")


def test_dell_lc_network_share_test_missing_action_does_not_post(redfish_mock):
    """A Dell LC fixture without TestNetworkShare returns an error and no POST."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.DellLcNetworkShareTest,
        "dell-lc-network-share-test",
    )

    assert isinstance(result, CommandResult)
    assert result.error == "action '#DellLCService.TestNetworkShare' not found"
    assert "/redfish/v1/Dell/Managers/iDRAC.Embedded.1/DellLCService" in (
        result.data["checked"]
    )


def test_dell_lc_network_share_test_registry_wiring():
    """The dell-lc-network-share-test command is wired into the registry."""
    registry = IDracManager.get_registry()

    assert registry[ApiRequestType.DellLcNetworkShareTest][
        "dell-lc-network-share-test"
    ] is DellLcNetworkShareTest
    cmd_parser, cmd_name, cmd_help = DellLcNetworkShareTest.register_subcommand(
        DellLcNetworkShareTest
    )
    args = {action.dest for action in cmd_parser._actions}
    assert cmd_name == "dell-lc-network-share-test"
    assert "network-share" in cmd_help
    assert {"host", "share_type", "proxy_support", "confirm", "dry_run"} <= args
