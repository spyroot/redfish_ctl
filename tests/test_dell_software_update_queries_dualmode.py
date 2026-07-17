"""Dual-mode-style coverage for Dell software-installation query actions."""
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.delloem.cmd_dell_software_update_queries import (
    DellSoftwareUpdateQueries,
)
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_manager_shared import ApiRequestType

REPO_ROOT = Path(__file__).resolve().parents[1]
DELL_CORPUS = corpus_dir(
    REPO_ROOT / "tests" / "dell_xr8620t_corpus.tar.gz",
    "10.252.252.209",
)
SOFTWARE_SERVICE = (
    "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/"
    "DellSoftwareInstallationService"
)
SCHEDULE_TARGET = (
    f"{SOFTWARE_SERVICE}/Actions/"
    "DellSoftwareInstallationService.GetUpdateSchedule"
)
REPO_LIST_TARGET = (
    f"{SOFTWARE_SERVICE}/Actions/"
    "DellSoftwareInstallationService.GetRepoBasedUpdateList"
)


@pytest.fixture
def dell_software_manager():
    """Serve the committed Dell XR8620t corpus over requests-mock.

    :return: tuple of Redfish manager and mock service.
    """
    requests_mock = pytest.importorskip("requests_mock")
    service = MockRedfishService(
        DELL_CORPUS,
        index=_build_fixture_index(DELL_CORPUS),
    )
    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=service.get_cb)
        mocker.post(requests_mock.ANY, text=service.post_cb)
        service.mocker = mocker
        yield (
            RedfishManagerBase(
                idrac_ip="mock-dell-software",
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


def test_dell_software_update_queries_list_targets_without_post(
    dell_software_manager,
):
    """Listing discovers Dell software update query actions without POSTing."""
    manager, service = dell_software_manager

    result = manager.sync_invoke(
        ApiRequestType.DellSoftwareUpdateQueries,
        "dell-software-update-queries",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    queries = {row["Query"]: row for row in result.data}
    assert set(queries) == {"repo-list", "schedule"}
    assert queries["schedule"]["Resource"] == SOFTWARE_SERVICE
    assert queries["schedule"]["Target"] == SCHEDULE_TARGET
    assert queries["repo-list"]["Target"] == REPO_LIST_TARGET
    assert _post_requests(service) == []


def test_dell_software_update_query_dry_run_resolves_target_without_post(
    dell_software_manager,
):
    """--dry_run resolves the selected read-only action without POSTing."""
    manager, service = dell_software_manager

    result = manager.sync_invoke(
        ApiRequestType.DellSoftwareUpdateQueries,
        "dell-software-update-queries",
        query="repo-list",
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == (
        "#DellSoftwareInstallationService.GetRepoBasedUpdateList"
    )
    assert result.data["target"] == REPO_LIST_TARGET
    assert result.data["payload"] == {}
    assert result.data["level"] == "read_only"
    assert result.data["blocked"] is None
    assert _post_requests(service) == []


def test_dell_software_update_query_posts_read_only_action_by_default(
    dell_software_manager,
):
    """Read-only Dell software query actions do not require a confirm flag."""
    manager, service = dell_software_manager

    result = manager.sync_invoke(
        ApiRequestType.DellSoftwareUpdateQueries,
        "dell-software-update-queries",
        query="schedule",
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == (
        "#DellSoftwareInstallationService.GetUpdateSchedule"
    )
    assert result.data["target"] == SCHEDULE_TARGET
    assert result.data["level"] == "read_only"
    assert len(posts) == 1
    assert posts[0].path.lower() == SCHEDULE_TARGET.lower()
    assert posts[0].json() == {}


def test_dell_software_update_query_missing_target_reports_without_post(
    redfish_mock_factory,
):
    """A non-Dell fixture reports the missing query action and sends no POST."""
    manager, service = redfish_mock_factory("generic")

    result = manager.sync_invoke(
        ApiRequestType.DellSoftwareUpdateQueries,
        "dell-software-update-queries",
        query="schedule",
    )

    assert isinstance(result, CommandResult)
    assert result.error == "Dell software update query not found: schedule"
    assert result.data == {"available": []}
    assert _post_requests(service) == []


def test_dell_software_update_queries_exposes_cli_entrypoint():
    """The command is wired into the package registry."""
    registry = RedfishManagerBase().get_registry()
    assert registry[ApiRequestType.DellSoftwareUpdateQueries][
        "dell-software-update-queries"
    ] is DellSoftwareUpdateQueries

    cmd_parser, cmd_name, cmd_help = (
        DellSoftwareUpdateQueries.register_subcommand(DellSoftwareUpdateQueries)
    )

    assert "--query" in cmd_parser.format_help()
    assert "--dry_run" in cmd_parser.format_help()
    assert cmd_name == "dell-software-update-queries"
    assert "Dell" in cmd_help
