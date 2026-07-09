"""Dual-mode test for the Redfish discovery command."""
import json

from redfish_ctl.discovery.cmd_discovery import Discovery
from redfish_ctl.idrac_shared import ApiRequestType, Singleton
from redfish_ctl.redfish_manager import CommandResult


def test_discovery_reads_service_root_in_mock_mode(
    redfish_api, redfish_service, monkeypatch, tmp_path
):
    """discovery reads the service root and queues linked resources."""
    service_root = {
        "@odata.id": "/redfish/v1/",
        "RedfishVersion": "1.11.0",
        "Systems": {"@odata.id": "/redfish/v1/Systems"},
        "Managers": {"@odata.id": "/redfish/v1/Managers"},
        "Chassis": {"@odata.id": "/redfish/v1/Chassis"},
    }
    redfish_service._overlay["/redfish/v1/"] = service_root
    monkeypatch.setenv("HOME", str(tmp_path))

    walked = []
    saved_visited = []

    def record_walk(self, resource_path, depth=0, max_depth=32):
        walked.append(resource_path)
        self.visited_urls[self.normalize_resource_path(resource_path)] = True

    def record_map_save(self):
        saved_visited.append(dict(self.visited_urls))

    Singleton._instances.pop(Discovery, None)
    monkeypatch.setattr(Discovery, "recursive_discovery", record_walk)
    monkeypatch.setattr(Discovery, "save_url_file_mapping", record_map_save)

    try:
        result = redfish_api.sync_invoke(ApiRequestType.Discovery, "discovery")
    finally:
        Singleton._instances.pop(Discovery, None)

    assert isinstance(result, CommandResult)
    assert result.data == service_root
    json.dumps(result.data)
    assert result.discovered is None
    assert result.extra is None
    assert result.error is None

    assert walked[0] == "/redfish/v1/"
    assert set(walked) == {
        "/redfish/v1/",
        "/redfish/v1/Systems",
        "/redfish/v1/Managers",
        "/redfish/v1/Chassis",
    }
    assert saved_visited == [
        {
            "/redfish/v1": True,
            "/redfish/v1/CompositionService": True,
            "/redfish/v1/Systems": True,
            "/redfish/v1/Managers": True,
            "/redfish/v1/Chassis": True,
        }
    ]

    assert {request.method for request in redfish_service.requests} == {"GET"}
    assert redfish_service.requests[0].path.rstrip("/").lower() == "/redfish/v1"
    discovery_dir = tmp_path / ".json_responses" / "mock-idrac"
    assert discovery_dir.is_dir()
    assert list(discovery_dir.iterdir()) == []
