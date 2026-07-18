"""Dual-mode-style coverage for Dell software update install actions."""

import json
from pathlib import Path

import pytest
from vendor_corpus import corpus_dir

from redfish_ctl.actions.action_policy import Destructiveness, classify
from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.delloem.cmd_dell_software_update_install import (
    DellSoftwareUpdateInstall,
)
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_manager_shared import ApiRequestType

DELL_CORPUS = corpus_dir(
    Path(__file__).parent / "dell_xr8620t_corpus.tar.gz",
    "10.252.252.209",
)
DELL_INDEX = {path.name.lower(): path for path in DELL_CORPUS.glob("*.json")}
SERVICE = (
    "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/"
    "DellSoftwareInstallationService"
)
REPOSITORY_TARGET = (
    f"{SERVICE}/Actions/"
    "DellSoftwareInstallationService.InstallFromRepository"
)
URI_TARGET = (
    f"{SERVICE}/Actions/"
    "DellSoftwareInstallationService.InstallFromURI"
)


def _fixture_for_path(path):
    """Return the extracted Dell fixture matching a Redfish path.

    :param path: request path from requests-mock.
    :return: fixture path, or None when the corpus lacks the resource.
    """
    name = "_" + path.strip("/").replace("/", "_") + ".json"
    return DELL_INDEX.get(name.lower())


def _service_body():
    """Return the DellSoftwareInstallationService fixture body.

    :return: parsed DellSoftwareInstallationService JSON.
    """
    return json.loads(_fixture_for_path(SERVICE).read_text())


@pytest.fixture
def dell_software_manager():
    """Serve the committed Dell corpus over requests-mock.

    :return: tuple of RedfishManagerBase, recorded requests, and GET overrides.
    """
    requests_mock = pytest.importorskip("requests_mock")
    requests = []
    overrides = {}

    def get_cb(request, context):
        requests.append(request)
        override = overrides.get(request.path.lower())
        if override is not None:
            context.status_code = 200
            return json.dumps(override)
        fixture = _fixture_for_path(request.path)
        if fixture is None:
            context.status_code = 404
            return json.dumps({"error": f"no fixture for {request.path}"})
        context.status_code = 200
        return fixture.read_text()

    def post_cb(request, context):
        requests.append(request)
        context.status_code = 202
        context.headers["Location"] = "/redfish/v1/TaskService/Tasks/software-install-1"
        return json.dumps({
            "Task": {
                "@odata.id": "/redfish/v1/TaskService/Tasks/software-install-1"
            }
        })

    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=get_cb)
        mocker.post(requests_mock.ANY, text=post_cb)
        manager = RedfishManagerBase(
            idrac_ip="mock-dell-software-install",
            idrac_username="root",
            idrac_password="mock",
            insecure=True,
            is_debug=False,
        )
        yield manager, requests, overrides


def _post_requests(requests):
    """Return POST requests recorded by the mock Redfish transport.

    :param requests: recorded requests-mock request objects.
    :return: list of POST requests.
    """
    return [request for request in requests if request.method == "POST"]


def test_dell_software_update_install_lists_targets(dell_software_manager):
    """Omitting --action lists install targets without POSTing."""
    manager, requests, _ = dell_software_manager

    result = manager.sync_invoke(
        ApiRequestType.DellSoftwareUpdateInstall,
        "dell-software-update-install",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert {
        (row["Action"], row["Target"])
        for row in result.data
    } == {
        ("repository", REPOSITORY_TARGET),
        ("uri", URI_TARGET),
    }
    repository = next(row for row in result.data if row["Action"] == "repository")
    uri = next(row for row in result.data if row["Action"] == "uri")
    assert repository["AllowableValues"]["ShareType"] == [
        "CIFS",
        "FTP",
        "HTTP",
        "HTTPS",
        "NFS",
        "TFTP",
    ]
    assert uri["AllowableValues"]["ProxyType"] == ["HTTP", "SOCKS"]
    assert _post_requests(requests) == []


def test_dell_software_update_install_repository_previews_payload(
    dell_software_manager,
):
    """InstallFromRepository resolves and previews payloads by default."""
    manager, requests, _ = dell_software_manager

    result = manager.sync_invoke(
        ApiRequestType.DellSoftwareUpdateInstall,
        "dell-software-update-install",
        action="repository",
        payload_json='{"Password": "placeholder-value"}',
        share_name="/repo/catalog",
        share_type="HTTP",
        apply_update="True",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert (
        result.data["action"]
        == "#DellSoftwareInstallationService.InstallFromRepository"
    )
    assert result.data["target"] == REPOSITORY_TARGET
    assert result.data["level"] == "destructive"
    assert result.data["blocked"] == "destructive action requires --confirm"
    assert result.data["payload"] == {
        "Password": "********",
        "ShareName": "/repo/catalog",
        "ShareType": "HTTP",
        "ApplyUpdate": "True",
    }
    assert _post_requests(requests) == []


def test_dell_software_update_install_repository_confirm_posts(
    dell_software_manager,
):
    """--confirm POSTs InstallFromRepository to the discovered target."""
    manager, requests, _ = dell_software_manager

    result = manager.sync_invoke(
        ApiRequestType.DellSoftwareUpdateInstall,
        "dell-software-update-install",
        action="repository",
        share_name="/repo/catalog",
        share_type="HTTPS",
        apply_update="False",
        confirm=True,
    )

    posts = _post_requests(requests)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert (
        result.data["action"]
        == "#DellSoftwareInstallationService.InstallFromRepository"
    )
    assert result.data["target"] == REPOSITORY_TARGET
    assert result.data["task_id"] == "software-install-1"
    assert len(posts) == 1
    assert posts[0].path.lower() == REPOSITORY_TARGET.lower()
    assert posts[0].json() == {
        "ShareName": "/repo/catalog",
        "ShareType": "HTTPS",
        "ApplyUpdate": "False",
    }


def test_dell_software_update_install_uri_confirm_posts(dell_software_manager):
    """--confirm POSTs InstallFromURI to the discovered target."""
    manager, requests, _ = dell_software_manager

    result = manager.sync_invoke(
        ApiRequestType.DellSoftwareUpdateInstall,
        "dell-software-update-install",
        action="uri",
        install_uri="https://repo.example.test/firmware.exe",
        ignore_cert_warning="On",
        proxy_support="Off",
        confirm=True,
    )

    posts = _post_requests(requests)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#DellSoftwareInstallationService.InstallFromURI"
    assert result.data["target"] == URI_TARGET
    assert len(posts) == 1
    assert posts[0].path.lower() == URI_TARGET.lower()
    assert posts[0].json() == {
        "URI": "https://repo.example.test/firmware.exe",
        "IgnoreCertWarning": "On",
        "ProxySupport": "Off",
    }


def test_dell_software_update_install_dry_run_overrides_confirm(
    dell_software_manager,
):
    """--dry_run keeps InstallFromURI from POSTing even with --confirm."""
    manager, requests, _ = dell_software_manager

    result = manager.sync_invoke(
        ApiRequestType.DellSoftwareUpdateInstall,
        "dell-software-update-install",
        action="uri",
        install_uri="https://repo.example.test/firmware.exe",
        confirm=True,
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] is None
    assert result.data["target"] == URI_TARGET
    assert _post_requests(requests) == []


def test_dell_software_update_install_rejects_invalid_allowable(
    dell_software_manager,
):
    """Inline allowable values reject invalid repository enum values."""
    manager, requests, _ = dell_software_manager

    result = manager.sync_invoke(
        ApiRequestType.DellSoftwareUpdateInstall,
        "dell-software-update-install",
        action="repository",
        share_name="/repo/catalog",
        share_type="SFTP",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "invalid value for DellSoftwareInstallationService.InstallFromRepository "
        "ShareType: SFTP; allowed: CIFS, FTP, HTTP, HTTPS, NFS, TFTP"
    )
    assert result.data["validation_errors"] == [
        {
            "parameter": "ShareType",
            "value": "SFTP",
            "allowed": ["CIFS", "FTP", "HTTP", "HTTPS", "NFS", "TFTP"],
        }
    ]
    assert _post_requests(requests) == []


def test_dell_software_update_install_reports_missing_action(
    dell_software_manager,
):
    """A service without InstallFromURI reports the remaining install action."""
    manager, requests, overrides = dell_software_manager
    body = _service_body()
    body["Actions"].pop("#DellSoftwareInstallationService.InstallFromURI")
    overrides[SERVICE.lower()] = body

    result = manager.sync_invoke(
        ApiRequestType.DellSoftwareUpdateInstall,
        "dell-software-update-install",
        action="uri",
        install_uri="https://repo.example.test/firmware.exe",
    )

    assert isinstance(result, CommandResult)
    assert result.error == "Dell software update install action not found: uri"
    assert [row["Action"] for row in result.data["available"]] == ["repository"]
    assert _post_requests(requests) == []


def test_dell_software_update_install_rejects_empty_payload(
    dell_software_manager,
):
    """Selected install actions require at least one payload field."""
    manager, requests, _ = dell_software_manager

    with pytest.raises(InvalidArgument, match="--action uri requires"):
        manager.sync_invoke(
            ApiRequestType.DellSoftwareUpdateInstall,
            "dell-software-update-install",
            action="uri",
        )

    assert _post_requests(requests) == []


def test_dell_software_update_install_rejects_non_object_json(
    dell_software_manager,
):
    """The payload JSON must be an object."""
    manager, requests, _ = dell_software_manager

    with pytest.raises(InvalidArgument, match="JSON object"):
        manager.sync_invoke(
            ApiRequestType.DellSoftwareUpdateInstall,
            "dell-software-update-install",
            action="repository",
            payload_json='["not-object"]',
        )

    assert _post_requests(requests) == []


def test_dell_software_update_install_rejects_missing_password_env(
    dell_software_manager,
):
    """Missing password environment variables fail before any POST."""
    manager, requests, _ = dell_software_manager

    with pytest.raises(
        InvalidArgument,
        match="environment variable 'MISSING_SOFTWARE_PASSWORD'",
    ):
        manager.sync_invoke(
            ApiRequestType.DellSoftwareUpdateInstall,
            "dell-software-update-install",
            action="repository",
            share_name="/repo/catalog",
            software_password_env="MISSING_SOFTWARE_PASSWORD",
            confirm=True,
        )

    assert _post_requests(requests) == []


def test_dell_software_update_install_is_registered():
    """The command is wired into the command registry and policy table."""
    registry = RedfishManagerBase().get_registry()
    assert (
        registry[ApiRequestType.DellSoftwareUpdateInstall][
            "dell-software-update-install"
        ]
        is DellSoftwareUpdateInstall
    )
    assert (
        classify("#DellSoftwareInstallationService.InstallFromRepository")
        == Destructiveness.DESTRUCTIVE
    )
    assert (
        classify("#DellSoftwareInstallationService.InstallFromURI")
        == Destructiveness.DESTRUCTIVE
    )

    cmd_parser, cmd_name, cmd_help = (
        DellSoftwareUpdateInstall.register_subcommand(DellSoftwareUpdateInstall)
    )
    help_text = cmd_parser.format_help()

    assert cmd_name == "dell-software-update-install"
    assert "software installation" in cmd_help
    assert "--action" in help_text
    assert "--payload-json" in help_text
    assert "--confirm" in help_text
