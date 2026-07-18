"""Dual-mode tests for Dell OS deployment driver-pack query action."""
import json
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.delloem.cmd_os_deployment_driver_pack import (
    DellOsDeploymentDriverPack,
)
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_manager_shared import ApiRequestType

REPO_ROOT = Path(__file__).resolve().parents[1]
DELL_CORPUS = corpus_dir(
    REPO_ROOT / "tests" / "dell_xr8620t_corpus.tar.gz",
    "10.252.252.209",
)
OS_DEPLOYMENT = (
    "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellOSDeploymentService"
)
DRIVER_PACK_TARGET = (
    f"{OS_DEPLOYMENT}/Actions/DellOSDeploymentService.GetDriverPackInfo"
)


@pytest.fixture
def dell_os_deployment_mock():
    """Return a manager and Dell XR8620t corpus-backed mock service.

    :return: tuple of Redfish manager and mock service.
    """
    requests_mock = pytest.importorskip("requests_mock")
    service = MockRedfishService(DELL_CORPUS, index=_build_fixture_index(DELL_CORPUS))

    def post_cb(request, context):
        service.requests.append(request)
        if request.path.lower() == DRIVER_PACK_TARGET.lower():
            context.status_code = 200
            return json.dumps({
                "DriverPackVersion": "42.7.9",
                "DriverPackStatus": "Available",
            })
        context.status_code = 404
        return json.dumps({"error": f"unexpected POST {request.path}"})

    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=service.get_cb)
        mocker.post(requests_mock.ANY, text=post_cb)
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


def _post_requests(service):
    """Return POST requests recorded by the mock Redfish service.

    :param service: mock Redfish service.
    :return: recorded POST requests.
    """
    return [request for request in service.requests if request.method == "POST"]


def test_dell_os_driver_pack_posts_query_and_preserves_response(
        dell_os_deployment_mock):
    """The read-only driver-pack action POSTs and returns the JSON body."""
    manager, service = dell_os_deployment_mock

    result = manager.sync_invoke(
        ApiRequestType.DellOsDeploymentDriverPack,
        "dell-os-driver-pack",
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#DellOSDeploymentService.GetDriverPackInfo"
    assert result.data["target"] == DRIVER_PACK_TARGET
    assert result.data["level"] == "read_only"
    assert result.data["response"] == {
        "DriverPackVersion": "42.7.9",
        "DriverPackStatus": "Available",
    }
    assert len(posts) == 1
    assert posts[0].path.lower() == DRIVER_PACK_TARGET.lower()
    assert posts[0].json() == {}


def test_dell_os_driver_pack_dry_run_does_not_post(dell_os_deployment_mock):
    """--dry_run resolves the target and returns a no-POST preview."""
    manager, service = dell_os_deployment_mock

    result = manager.sync_invoke(
        ApiRequestType.DellOsDeploymentDriverPack,
        "dell-os-driver-pack",
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == {
        "dry_run": True,
        "action": "#DellOSDeploymentService.GetDriverPackInfo",
        "target": DRIVER_PACK_TARGET,
        "payload": {},
        "level": "read_only",
        "blocked": None,
    }
    assert _post_requests(service) == []


def test_dell_os_driver_pack_missing_action_reports_available(
        dell_os_deployment_mock):
    """A service without GetDriverPackInfo returns a structured error."""
    manager, service = dell_os_deployment_mock
    service._overlay[OS_DEPLOYMENT] = {
        "@odata.id": OS_DEPLOYMENT,
        "Actions": {
            "#DellOSDeploymentService.GetAttachStatus": {
                "target": (
                    f"{OS_DEPLOYMENT}/Actions/"
                    "DellOSDeploymentService.GetAttachStatus"
                )
            }
        },
    }
    service._overlay[OS_DEPLOYMENT.lower()] = service._overlay[OS_DEPLOYMENT]

    result = manager.sync_invoke(
        ApiRequestType.DellOsDeploymentDriverPack,
        "dell-os-driver-pack",
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "action '#DellOSDeploymentService.GetDriverPackInfo' not found on "
        f"{OS_DEPLOYMENT}"
    )
    assert result.data["action"] == "#DellOSDeploymentService.GetDriverPackInfo"
    assert result.data["available"] == [
        "#DellOSDeploymentService.GetAttachStatus",
        "GetAttachStatus",
    ]
    assert _post_requests(service) == []


def test_dell_os_driver_pack_exposes_cli_entrypoint():
    """The dell-os-driver-pack command is wired into the package registry."""
    registry = RedfishManagerBase().get_registry()
    assert (
        registry[ApiRequestType.DellOsDeploymentDriverPack]["dell-os-driver-pack"]
        is DellOsDeploymentDriverPack
    )

    cmd_parser, cmd_name, cmd_help = (
        DellOsDeploymentDriverPack.register_subcommand(
            DellOsDeploymentDriverPack
        )
    )

    assert "--dry_run" in cmd_parser.format_help()
    assert cmd_name == "dell-os-driver-pack"
    assert "driver-pack" in cmd_help
