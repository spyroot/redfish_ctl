"""Dual-mode-style coverage for Dell iDRAC KVM session status."""

import json
from contextlib import contextmanager
from pathlib import Path

import pytest
from vendor_corpus import corpus_dir

from redfish_ctl.oem.cmd_dell_kvm_session import DellKvmSession
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_manager_shared import ApiRequestType

DELL_CORPUS = corpus_dir(
    Path(__file__).parent / "dell_xr8620t_corpus.tar.gz", "10.252.252.209"
)
DELL_INDEX = {path.name.lower(): path for path in DELL_CORPUS.glob("*.json")}
CARD_SERVICE = (
    "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DelliDRACCardService"
)
KVM_ACTION = "#DelliDRACCardService.GetKVMSession"
KVM_TARGET = f"{CARD_SERVICE}/Actions/DelliDRACCardService.GetKVMSession"


def _fixture_for_path(path):
    """Return the extracted Dell fixture matching a Redfish path.

    :param path: request path from requests-mock.
    :return: fixture path, or None when the corpus lacks the resource.
    """
    name = "_" + path.strip("/").replace("/", "_") + ".json"
    return DELL_INDEX.get(name.lower())


@contextmanager
def _mock_dell_kvm(card_service_transform=None):
    """Serve the committed Dell corpus over requests-mock.

    :param card_service_transform: optional mutator for the card-service fixture.
    :return: context yielding RedfishManagerBase and recorded requests.
    """
    requests_mock = pytest.importorskip("requests_mock")
    requests = []

    def get_cb(request, context):
        requests.append(request)
        fixture = _fixture_for_path(request.path)
        if fixture is None:
            context.status_code = 404
            return json.dumps({"error": f"no fixture for {request.path}"})
        data = json.loads(fixture.read_text())
        if request.path.rstrip("/") == CARD_SERVICE and card_service_transform:
            data = card_service_transform(data)
        context.status_code = 200
        return json.dumps(data)

    def post_cb(request, context):
        requests.append(request)
        context.status_code = 200
        return json.dumps({
            "Name": "KVM session status",
            "KVMSession": {
                "Active": False,
                "SessionCount": 0,
            },
        })

    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=get_cb)
        mocker.post(requests_mock.ANY, text=post_cb)
        manager = RedfishManagerBase(
            idrac_ip="mock-dell-kvm",
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


def test_dell_kvm_session_lists_target_without_posting():
    """With no query flag, the command lists the KVM action target only."""
    with _mock_dell_kvm() as (manager, requests):
        result = manager.sync_invoke(ApiRequestType.DellKvmSession, "dell-kvm-session")

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == {
        "card_service": CARD_SERVICE,
        "action": KVM_ACTION,
        "target": KVM_TARGET,
    }
    assert _post_requests(requests) == []


def test_dell_kvm_session_dry_run_resolves_without_posting():
    """--query --dry_run reports the read-only POST target without firing it."""
    with _mock_dell_kvm() as (manager, requests):
        result = manager.sync_invoke(
            ApiRequestType.DellKvmSession,
            "dell-kvm-session",
            query=True,
            dry_run=True,
        )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == {
        "dry_run": True,
        "action": KVM_ACTION,
        "target": KVM_TARGET,
        "payload": {},
        "level": "read_only",
        "blocked": None,
    }
    assert _post_requests(requests) == []


def test_dell_kvm_session_query_posts_and_preserves_response():
    """--query POSTs an empty body and returns the KVM session response body."""
    with _mock_dell_kvm() as (manager, requests):
        result = manager.sync_invoke(
            ApiRequestType.DellKvmSession,
            "dell-kvm-session",
            query=True,
        )

    posts = _post_requests(requests)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["Name"] == "KVM session status"
    assert result.data["KVMSession"] == {
        "Active": False,
        "SessionCount": 0,
    }
    assert result.data["Status"] == "ok"
    assert result.data["executed"] is True
    assert result.data["method"] == "POST"
    assert result.data["action"] == KVM_ACTION
    assert result.data["target"] == KVM_TARGET
    assert result.data["level"] == "read_only"
    assert len(posts) == 1
    assert posts[0].path.lower() == KVM_TARGET.lower()
    assert posts[0].json() == {}


def test_dell_kvm_session_reports_missing_action_without_posting():
    """A card-service resource without GetKVMSession reports available actions."""

    def without_kvm_action(data):
        actions = data.get("Actions", {})
        actions.pop(KVM_ACTION, None)
        return data

    with _mock_dell_kvm(without_kvm_action) as (manager, requests):
        result = manager.sync_invoke(
            ApiRequestType.DellKvmSession,
            "dell-kvm-session",
            query=True,
        )

    assert isinstance(result, CommandResult)
    assert result.error == f"action '{KVM_ACTION}' not found on {CARD_SERVICE}"
    assert KVM_ACTION not in result.data["available"]
    assert "DeleteGroup" in result.data["available"]
    assert _post_requests(requests) == []


def test_dell_kvm_session_exposes_cli_entrypoint():
    """The dell-kvm-session command is wired into the package registry."""
    registry = RedfishManagerBase().get_registry()
    assert registry[ApiRequestType.DellKvmSession]["dell-kvm-session"] is (
        DellKvmSession
    )

    cmd_parser, cmd_name, cmd_help = DellKvmSession.register_subcommand(
        DellKvmSession
    )

    assert "--query" in cmd_parser.format_help()
    assert "--dry_run" in cmd_parser.format_help()
    assert cmd_name == "dell-kvm-session"
    assert "KVM" in cmd_help
