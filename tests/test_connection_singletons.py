"""Regression tests for per-connection command singleton isolation."""

from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from redfish_ctl.api import get_system
from redfish_ctl.fleet import cmd_fleet
from redfish_ctl.fleet.cmd_fleet import FleetNode, read_fleet
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.system.cmd_system import SystemQuery

HOSTS = {
    "node-a.example.test": {
        "vendor": "VendorA",
        "manager": "BMC-A",
        "system": "System-A",
        "power_state": "On",
        "health": "OK",
    },
    "node-b.example.test": {
        "vendor": "VendorB",
        "manager": "BMC-B",
        "system": "System-B",
        "power_state": "Off",
        "health": "Warning",
    },
}


def _manager(host: str) -> IDracManager:
    return IDracManager(
        idrac_ip=host,
        idrac_username="root",
        idrac_password="mock",
        insecure=True,
        is_http=True,
    )


def _payload(host: str, path: str) -> dict:
    spec = HOSTS[host]
    manager_uri = f"/redfish/v1/Managers/{spec['manager']}"
    system_uri = f"/redfish/v1/Systems/{spec['system']}"
    if path == "/redfish/v1/":
        return {
            "RedfishVersion": "1.16.0",
            "Vendor": spec["vendor"],
            "Managers": {"@odata.id": "/redfish/v1/Managers"},
            "Systems": {"@odata.id": "/redfish/v1/Systems"},
        }
    if path == "/redfish/v1/Managers":
        return {
            "Members@odata.count": 1,
            "Members": [{"@odata.id": manager_uri}],
        }
    if path == manager_uri:
        return {
            "Id": spec["manager"],
            "Links": {"ManagerForServers": [{"@odata.id": system_uri}]},
        }
    if path == "/redfish/v1/Systems":
        return {
            "Members@odata.count": 1,
            "Members": [{"@odata.id": system_uri}],
        }
    if path == system_uri:
        return {
            "Id": spec["system"],
            "Name": f"Host {spec['system']}",
            "PowerState": spec["power_state"],
            "Status": {"Health": spec["health"], "State": "Enabled"},
        }
    raise KeyError(path)


@pytest.fixture
def two_host_redfish():
    """Serve two distinct BMC roots through requests-mock."""
    requests_mock = pytest.importorskip("requests_mock")
    seen = []

    def get_cb(request, context):
        parsed = urlparse(request.url)
        host = parsed.hostname
        seen.append((host, parsed.path))
        try:
            body = _payload(host, parsed.path)
        except KeyError:
            context.status_code = 404
            return {"error": f"no fixture for {host}{parsed.path}"}
        context.status_code = 200
        return body

    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, json=get_cb)
        yield seen


def test_command_singletons_are_isolated_by_connection(two_host_redfish):
    """The same command class must read the BMC from the current connection."""
    first = _manager("node-a.example.test").sync_invoke(
        ApiRequestType.SystemQuery,
        "system_query",
    )
    second = _manager("node-b.example.test").sync_invoke(
        ApiRequestType.SystemQuery,
        "system_query",
    )

    assert first.data["PowerState"] == "On"
    assert second.data["PowerState"] == "Off"
    assert ("node-a.example.test", "/redfish/v1/Systems/System-A") in two_host_redfish
    assert ("node-b.example.test", "/redfish/v1/Systems/System-B") in two_host_redfish


def test_cached_properties_are_isolated_by_connection(two_host_redfish):
    """Cached Redfish root properties must remain per BMC connection."""
    first = SystemQuery(
        idrac_ip="node-a.example.test",
        idrac_username="root",
        idrac_password="mock",
        insecure=True,
        is_http=True,
    )
    second = SystemQuery(
        idrac_ip="node-b.example.test",
        idrac_username="root",
        idrac_password="mock",
        insecure=True,
        is_http=True,
    )

    assert first is not second
    assert first.redfish_vendor == "VendorA"
    assert second.redfish_vendor == "VendorB"


def test_fleet_reads_each_node_with_its_own_connection(monkeypatch, two_host_redfish):
    """Fleet inventory must not reuse the first node's command instance."""
    get_system(_manager("node-a.example.test"))
    monkeypatch.setattr(cmd_fleet, "get_sensors", lambda manager: ())
    monkeypatch.setattr(
        cmd_fleet,
        "get_thermal",
        lambda manager: SimpleNamespace(temperatures=()),
    )
    nodes = (
        FleetNode(
            name="node-a",
            address="node-a.example.test",
            username="root",
            password="mock",
            port=443,
            insecure=True,
            use_http=True,
        ),
        FleetNode(
            name="node-b",
            address="node-b.example.test",
            username="root",
            password="mock",
            port=443,
            insecure=True,
            use_http=True,
        ),
    )

    data = read_fleet(nodes, concurrency=2)

    assert [row["powerState"] for row in data["nodes"]] == ["On", "Off"]
    assert data["summary"] == {"total": 2, "ok": 2, "failed": 0}

