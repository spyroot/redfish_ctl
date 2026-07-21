"""Dual-mode-style tests for Dell hardware proof verification."""

from copy import deepcopy
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.actions.action_policy import classify
from redfish_ctl.oem.cmd_dell_card_hw_proof import DellCardHwProof
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_manager_shared import ApiRequestType

REPO_ROOT = Path(__file__).resolve().parents[1]
DELL_CORPUS = corpus_dir(
    REPO_ROOT / "tests" / "dell_xr8620t_corpus.tar.gz",
    "10.252.252.209",
)
GENERIC_MANAGER_URI = "/redfish/v1/Managers/BMC"
SERVICE_URI = "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DelliDRACCardService"
TARGET_URI = (
    "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DelliDRACCardService/"
    "Actions/DelliDRACCardService.VerifyHWProofOfPossession"
)
ACTION_TYPE = "#DelliDRACCardService.VerifyHWProofOfPossession"


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
            RedfishManagerBase(
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
    """Overlay a Dell card service link and optional proof action."""
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
            "Algorithm@Redfish.AllowableValues": ["AES128CBC"],
            "KeyDerivationFunction@Redfish.AllowableValues": ["DellSHA256"],
        }

    for path, body in (
        (GENERIC_MANAGER_URI, manager),
        (SERVICE_URI, service),
    ):
        redfish_service._overlay[path] = body
        redfish_service._overlay[path.lower()] = body


def test_dell_card_hw_proof_lists_corpus_target(dell_corpus_mock):
    """Listing discovers the Dell corpus hardware proof action."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellCardHwProof,
        "dell-card-hw-proof",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["hw_proof_targets"] == [{
        "Resource": SERVICE_URI,
        "Action": ACTION_TYPE,
        "Target": TARGET_URI,
        "AllowedAlgorithms": ["AES128CBC"],
        "AllowedKeyDerivationFunctions": ["DellSHA256"],
    }]
    assert _post_requests(service) == []


def test_dell_card_hw_proof_previews_default_payload(dell_corpus_mock):
    """A selected proof verification previews unless --confirm is supplied."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellCardHwProof,
        "dell-card-hw-proof",
        dry_run=True,
    )

    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == ACTION_TYPE
    assert result.data["level"] == "read_only"
    assert result.data["target"] == TARGET_URI
    assert result.data["payload"] == {
        "Algorithm": "AES128CBC",
        "KeyDerivationFunction": "DellSHA256",
    }
    assert result.data["requires_confirm"] is True
    assert result.data["blocked"] == (
        "Dell hardware proof verification requires --confirm"
    )
    assert _post_requests(service) == []


def test_dell_card_hw_proof_confirm_posts_payload(dell_corpus_mock):
    """--confirm POSTs one Dell hardware proof verification request."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellCardHwProof,
        "dell-card-hw-proof",
        algorithm="AES128CBC",
        key_derivation_function="DellSHA256",
        confirm=True,
    )

    posts = _post_requests(service)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == ACTION_TYPE
    assert result.data["level"] == "read_only"
    assert len(posts) == 1
    assert posts[0].path.lower() == TARGET_URI.lower()
    assert posts[0].json() == {
        "Algorithm": "AES128CBC",
        "KeyDerivationFunction": "DellSHA256",
    }


def test_dell_card_hw_proof_rejects_invalid_algorithm(dell_corpus_mock):
    """Inline Redfish metadata rejects unsupported Algorithm values."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellCardHwProof,
        "dell-card-hw-proof",
        algorithm="AES256CBC",
        key_derivation_function="DellSHA256",
        confirm=True,
    )

    assert result.error == (
        "invalid value for DelliDRACCardService.VerifyHWProofOfPossession "
        "Algorithm: AES256CBC; allowed: AES128CBC"
    )
    assert result.data["validation_errors"] == [{
        "parameter": "Algorithm",
        "value": "AES256CBC",
        "allowed": ["AES128CBC"],
    }]
    assert _post_requests(service) == []


def test_dell_card_hw_proof_missing_action_reports_without_post(
    redfish_mock,
    redfish_service,
):
    """A card-service resource without the proof action returns a target error."""
    _seed_card_service(redfish_service, include_action=False)

    result = redfish_mock.sync_invoke(
        ApiRequestType.DellCardHwProof,
        "dell-card-hw-proof",
        confirm=True,
    )

    assert result.error == "Dell hardware proof action not found"
    assert result.data == {"action": ACTION_TYPE, "available": []}
    assert _post_requests(redfish_service) == []


def test_dell_card_hw_proof_exposes_policy_and_cli_entrypoint():
    """The dell-card-hw-proof command is wired into policy and registry."""
    assert classify(ACTION_TYPE).value == "read_only"

    registry = RedfishManagerBase().get_registry()
    assert registry[ApiRequestType.DellCardHwProof]["dell-card-hw-proof"] is (
        DellCardHwProof
    )

    cmd_parser, cmd_name, cmd_help = DellCardHwProof.register_subcommand(
        DellCardHwProof
    )
    help_text = cmd_parser.format_help()

    assert "--algorithm" in help_text
    assert "--key-derivation-function" in help_text
    assert "--resource-uri" in help_text
    assert "--confirm" in help_text
    assert "--dry_run" in help_text
    assert cmd_name == "dell-card-hw-proof"
    assert "hardware proof" in cmd_help
