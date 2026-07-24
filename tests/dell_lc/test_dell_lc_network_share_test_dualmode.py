"""Dual-mode-style coverage for DellLCService.TestNetworkShare."""
import json
from pathlib import Path

import pytest
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
DELL_INDEX = {path.name.lower(): path for path in DELL_CORPUS.glob("*.json")}
SERVICE_URI = "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellLCService"
TARGET_URI = f"{SERVICE_URI}/Actions/DellLCService.TestNetworkShare"


def _fixture_for_path(path):
    """Return the extracted Dell fixture matching a Redfish path.

    :param path: requests-mock request path.
    :return: fixture path, or None when the corpus lacks the resource.
    """
    name = "_" + path.strip("/").replace("/", "_") + ".json"
    return DELL_INDEX.get(name.lower())


@pytest.fixture
def dell_lc_manager():
    """Serve the committed Dell corpus over requests-mock.

    :return: tuple of IDracManager and recorded requests.
    """
    requests_mock = pytest.importorskip("requests_mock")
    requests = []

    def get_cb(request, context):
        requests.append(request)
        fixture = _fixture_for_path(request.path)
        if fixture is None:
            context.status_code = 404
            return json.dumps({"error": f"no fixture for {request.path}"})
        context.status_code = 200
        return fixture.read_text()

    def post_cb(request, context):
        requests.append(request)
        context.status_code = 200
        return json.dumps({
            "@Message.ExtendedInfo": [{
                "MessageId": "Base.1.12.Success",
                "Message": "Successfully Completed Request",
                "Severity": "OK",
            }]
        })

    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=get_cb)
        mocker.post(requests_mock.ANY, text=post_cb)
        manager = IDracManager(
            idrac_ip="mock-dell-lc",
            idrac_username="root",
            idrac_password="mock",
            insecure=True,
            is_debug=False,
        )
        yield manager, requests


def _post_requests(requests):
    """Return POST requests recorded by the mock Redfish transport.

    :param requests: recorded requests-mock request objects.
    :return: list of POST requests.
    """
    return [request for request in requests if request.method == "POST"]


def test_dell_lc_network_share_test_lists_target_without_mutating(
    dell_lc_manager,
):
    """Without --host, the command lists the discovered action target only."""
    manager, requests = dell_lc_manager

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
    assert _post_requests(requests) == []


def test_dell_lc_network_share_test_without_confirm_is_preview_only(
    dell_lc_manager,
):
    """A host payload is resolved but not POSTed unless --confirm is present."""
    manager, requests = dell_lc_manager

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
    assert _post_requests(requests) == []


def test_dell_lc_network_share_test_confirm_posts_payload(dell_lc_manager):
    """--confirm POSTs TestNetworkShare to the discovered action target."""
    manager, requests = dell_lc_manager

    result = manager.sync_invoke(
        ApiRequestType.DellLcNetworkShareTest,
        "dell-lc-network-share-test",
        host="repo.example.test",
        share_type="HTTPS",
        proxy_support="ParametersProxy",
        ignore_cert_warning="Off",
        confirm=True,
    )

    posts = _post_requests(requests)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#DellLCService.TestNetworkShare"
    assert result.data["target"] == TARGET_URI
    assert result.data["level"] == "reversible"
    assert len(posts) == 1
    assert posts[0].path.lower() == TARGET_URI.lower()
    assert posts[0].json() == {
        "IPAddress": "repo.example.test",
        "ShareType": "HTTPS",
        "ProxySupport": "ParametersProxy",
        "IgnoreCertWarning": "Off",
    }


def test_dell_lc_network_share_test_rejects_invalid_share_type(
    dell_lc_manager,
):
    """Inline allowable values reject an unsupported ShareType before POST."""
    manager, requests = dell_lc_manager

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
    assert _post_requests(requests) == []


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
