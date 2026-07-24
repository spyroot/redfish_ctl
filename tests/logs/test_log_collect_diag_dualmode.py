"""Dual-mode-style coverage for collecting LogService diagnostic data."""

import json
from pathlib import Path

import pytest
from vendor_corpus import corpus_dir

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

GB300_NODE2_CORPUS = corpus_dir(
    Path(__file__).parent.parent / "nvidia_gb300_node2_corpus.tar.gz", "172.25.230.20"
)
GB300_NODE2_INDEX = {path.name.lower(): path for path in GB300_NODE2_CORPUS.glob("*.json")}
MANAGER_DUMP = "/redfish/v1/Managers/BMC_0/LogServices/Dump"
COLLECT_TARGET = f"{MANAGER_DUMP}/Actions/LogService.CollectDiagnosticData"


def _fixture_for_path(path):
    """Return the extracted node2 fixture matching a Redfish path."""
    name = "_" + path.strip("/").replace("/", "_") + ".json"
    return GB300_NODE2_INDEX.get(name.lower())


@pytest.fixture
def gb300_node2_manager():
    """Serve the committed GB300 node2 corpus over requests-mock."""
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
        context.status_code = 202
        context.headers["Location"] = "/redfish/v1/TaskService/Tasks/collect-1"
        return json.dumps({"Task": {"@odata.id": "/redfish/v1/TaskService/Tasks/collect-1"}})

    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=get_cb)
        mocker.post(requests_mock.ANY, text=post_cb)
        manager = IDracManager(
            idrac_ip="mock-gb300-node2",
            idrac_username="root",
            idrac_password="mock",
            insecure=True,
            is_debug=False,
        )
        yield manager, requests


def _post_requests(requests):
    """Return POST requests recorded by the mock Redfish transport."""
    return [request for request in requests if request.method == "POST"]


def test_log_collect_diag_lists_services_without_mutating(gb300_node2_manager):
    """With no target, the command lists collectable LogServices and never POSTs."""
    manager, requests = gb300_node2_manager

    result = manager.sync_invoke(ApiRequestType.LogCollectDiagnosticData, "log-collect-diag")

    assert isinstance(result, CommandResult)
    assert result.error is None
    services = result.data["collectable_log_services"]
    assert any(service["uri"] == MANAGER_DUMP for service in services)
    assert _post_requests(requests) == []


def test_log_collect_diag_without_confirm_is_preview_only(gb300_node2_manager):
    """CollectDiagnosticData resolves the target but does not POST without --confirm."""
    manager, requests = gb300_node2_manager

    result = manager.sync_invoke(
        ApiRequestType.LogCollectDiagnosticData,
        "log-collect-diag",
        log_service=MANAGER_DUMP,
        diagnostic_data_type="Manager",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == "#LogService.CollectDiagnosticData"
    assert result.data["target"] == COLLECT_TARGET
    assert result.data["payload"] == {"DiagnosticDataType": "Manager"}
    assert result.data["level"] == "reversible"
    assert result.data["blocked"] == "diagnostic data collection requires --confirm"
    assert _post_requests(requests) == []


def test_log_collect_diag_confirm_posts_payload(gb300_node2_manager):
    """--confirm POSTs the requested diagnostic-data type to the discovered action."""
    manager, requests = gb300_node2_manager

    result = manager.sync_invoke(
        ApiRequestType.LogCollectDiagnosticData,
        "log-collect-diag",
        log_service=MANAGER_DUMP,
        diagnostic_data_type="Manager",
        confirm=True,
    )

    posts = _post_requests(requests)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#LogService.CollectDiagnosticData"
    assert result.data["target"] == COLLECT_TARGET
    assert result.data["level"] == "reversible"
    assert result.data["task_id"] == "collect-1"
    assert len(posts) == 1
    assert posts[0].path.lower() == COLLECT_TARGET.lower()
    assert posts[0].json() == {"DiagnosticDataType": "Manager"}


def test_log_collect_diag_confirm_dry_run_still_does_not_post(gb300_node2_manager):
    """--dry_run remains a no-POST preview even when --confirm is also present."""
    manager, requests = gb300_node2_manager

    result = manager.sync_invoke(
        ApiRequestType.LogCollectDiagnosticData,
        "log-collect-diag",
        log_service=MANAGER_DUMP,
        diagnostic_data_type="Manager",
        confirm=True,
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] is None
    assert result.data["target"] == COLLECT_TARGET
    assert result.data["payload"] == {"DiagnosticDataType": "Manager"}
    assert _post_requests(requests) == []


def test_log_collect_diag_oem_payload_includes_oem_type(gb300_node2_manager):
    """OEM diagnostic-data collection includes the OEM type when supplied."""
    manager, requests = gb300_node2_manager

    result = manager.sync_invoke(
        ApiRequestType.LogCollectDiagnosticData,
        "log-collect-diag",
        log_service=MANAGER_DUMP,
        diagnostic_data_type="OEM",
        oem_diagnostic_data_type="NvidiaDump",
        dry_run=True,
    )

    assert result.error is None
    assert result.data["payload"] == {
        "DiagnosticDataType": "OEM",
        "OEMDiagnosticDataType": "NvidiaDump",
    }
    assert result.data["blocked"] is None
    assert _post_requests(requests) == []


def test_log_collect_diag_ambiguous_id_requires_uri(gb300_node2_manager):
    """The GB300 corpus exposes several Dump services, so Id-only targeting is rejected."""
    manager, requests = gb300_node2_manager

    with pytest.raises(InvalidArgument, match="ambiguous"):
        manager.sync_invoke(
            ApiRequestType.LogCollectDiagnosticData,
            "log-collect-diag",
            log_service="Dump",
            diagnostic_data_type="Manager",
        )

    assert _post_requests(requests) == []


def test_log_collect_diag_no_capable_services_raises(redfish_mock_factory):
    """A corpus without CollectDiagnosticData fails clearly before any POST."""
    manager, service = redfish_mock_factory("hpe")

    with pytest.raises(InvalidArgument, match="no diagnostic data collection-capable"):
        manager.sync_invoke(
            ApiRequestType.LogCollectDiagnosticData,
            "log-collect-diag",
            log_service="IML",
            diagnostic_data_type="Manager",
        )

    assert _post_requests(service.requests) == []
