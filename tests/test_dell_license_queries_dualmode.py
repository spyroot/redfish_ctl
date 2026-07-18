"""Dual-mode-style coverage for Dell OEM license-management queries."""

import json
from pathlib import Path

import pytest
from vendor_corpus import corpus_dir

from redfish_ctl.actions.action_policy import Destructiveness, classify
from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.licenses.cmd_dell_license_queries import DellLicenseQueries
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_manager_shared import ApiRequestType

DELL_CORPUS = corpus_dir(
    Path(__file__).parent / "dell_xr8620t_corpus.tar.gz", "10.252.252.209"
)
DELL_INDEX = {path.name.lower(): path for path in DELL_CORPUS.glob("*.json")}
SERVICE_URI = (
    "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/"
    "DellLicenseManagementService"
)
LICENSE_COLLECTION = "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellLicenses"
SHOW_BITS_ACTION = "#DellLicenseManagementService.ShowLicenseBits"
SHOW_BITS_TARGET = (
    f"{SERVICE_URI}/Actions/DellLicenseManagementService.ShowLicenseBits"
)


def _fixture_for_path(path):
    """Return the extracted Dell fixture matching a Redfish path.

    :param path: request path from requests-mock.
    :return: fixture path, or None when the corpus lacks the resource.
    """
    name = "_" + path.strip("/").replace("/", "_") + ".json"
    return DELL_INDEX.get(name.lower())


@pytest.fixture
def dell_license_query_manager():
    """Serve the committed Dell corpus over requests-mock.

    :return: tuple of RedfishManagerBase and recorded requests list.
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
            "LicenseBits": {
                "Datacenter": True,
                "Enterprise": False,
            },
        })

    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=get_cb)
        mocker.post(requests_mock.ANY, text=post_cb)
        manager = RedfishManagerBase(
            idrac_ip="mock-dell-license-query",
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


def test_dell_license_queries_lists_targets_without_mutating(
    dell_license_query_manager,
):
    """With no selected query, the command lists metadata and never POSTs."""
    manager, requests = dell_license_query_manager

    result = manager.sync_invoke(
        ApiRequestType.DellLicenseQueries,
        "dell-license-queries",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["license_management_service"] == SERVICE_URI
    assert result.data["license_collection"] == LICENSE_COLLECTION
    assert result.data["queries"] == {
        "show-license-bits": {
            "action": SHOW_BITS_ACTION,
            "target": SHOW_BITS_TARGET,
            "level": "read_only",
        },
    }
    action_names = {row["name"] for row in result.data["actions"]}
    assert {
        "DeleteLicense",
        "ExportLicense",
        "ImportLicenseFromNetworkShare",
        "ShowLicenseBits",
    } <= action_names
    assert _post_requests(requests) == []


def test_dell_license_queries_posts_selected_read_only_query(
    dell_license_query_manager,
):
    """The explicit show-license-bits query POSTs an empty payload."""
    manager, requests = dell_license_query_manager

    result = manager.sync_invoke(
        ApiRequestType.DellLicenseQueries,
        "dell-license-queries",
        query="show-license-bits",
    )

    posts = _post_requests(requests)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["LicenseBits"] == {
        "Datacenter": True,
        "Enterprise": False,
    }
    assert result.data["executed"] is True
    assert result.data["method"] == "POST"
    assert result.data["query"] == "show-license-bits"
    assert result.data["action"] == SHOW_BITS_ACTION
    assert result.data["target"] == SHOW_BITS_TARGET
    assert result.data["level"] == "read_only"
    assert len(posts) == 1
    assert posts[0].path.lower() == SHOW_BITS_TARGET.lower()
    assert posts[0].json() == {}


def test_dell_license_queries_dry_run_does_not_post(dell_license_query_manager):
    """--dry_run resolves the selected query without POSTing."""
    manager, requests = dell_license_query_manager

    result = manager.sync_invoke(
        ApiRequestType.DellLicenseQueries,
        "dell-license-queries",
        query="show-license-bits",
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == {
        "dry_run": True,
        "query": "show-license-bits",
        "action": SHOW_BITS_ACTION,
        "target": SHOW_BITS_TARGET,
        "payload": {},
        "level": "read_only",
        "blocked": None,
    }
    assert _post_requests(requests) == []


def test_dell_license_queries_missing_action_reports_available(
    dell_license_query_manager,
    monkeypatch,
):
    """A service without ShowLicenseBits fails closed with available actions."""
    manager, requests = dell_license_query_manager

    def service_without_show_bits(self, do_async):
        fixture = _fixture_for_path(SERVICE_URI)
        data = json.loads(fixture.read_text())
        data["Actions"].pop(SHOW_BITS_ACTION)
        return SERVICE_URI, data

    monkeypatch.setattr(
        DellLicenseQueries,
        "_license_management_service",
        service_without_show_bits,
    )

    result = manager.sync_invoke(
        ApiRequestType.DellLicenseQueries,
        "dell-license-queries",
        query="show-license-bits",
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        f"action '{SHOW_BITS_ACTION}' not found on {SERVICE_URI}"
    )
    assert SHOW_BITS_ACTION not in result.data["available"]
    assert "DeleteLicense" in result.data["available"]
    assert _post_requests(requests) == []


def test_dell_license_queries_rejects_unknown_programmatic_query(
    dell_license_query_manager,
):
    """Programmatic callers get a clear error for unsupported query aliases."""
    manager, _ = dell_license_query_manager

    with pytest.raises(InvalidArgument, match="unsupported Dell license query"):
        manager.sync_invoke(
            ApiRequestType.DellLicenseQueries,
            "dell-license-queries",
            query="delete-license",
        )


def test_dell_license_queries_policy_and_registry():
    """The query action is read-only and the command self-registers."""
    registry = RedfishManagerBase.get_registry()

    assert classify(SHOW_BITS_ACTION) is Destructiveness.READ_ONLY
    assert "dell-license-queries" in registry[ApiRequestType.DellLicenseQueries]

    cmd_parser, cmd_name, cmd_help = registry[ApiRequestType.DellLicenseQueries][
        "dell-license-queries"
    ].register_subcommand(
        registry[ApiRequestType.DellLicenseQueries]["dell-license-queries"]
    )
    assert cmd_name == "dell-license-queries"
    assert "license-management" in cmd_help
    parsed = cmd_parser.parse_args(["--query", "show-license-bits", "--dry_run"])
    assert parsed.query == "show-license-bits"
    assert parsed.dry_run is True
