"""Offline tests for the fleet inventory read command."""

import json
from pathlib import Path

import pytest
import yaml
from vendor_corpus import corpus_dir

from redfish_ctl.fleet import cmd_fleet
from redfish_ctl.fleet.cmd_fleet import FleetInventory, FleetNode, read_fleet

GB300_CORPUS = corpus_dir(
    Path(__file__).parent.parent / "supermicro_gb300_corpus.tar.gz", "172.25.230.37"
)
GB300_INDEX = {path.name.lower(): path for path in GB300_CORPUS.glob("*.json")}


def _fixture_for_path(path):
    name = "_" + path.strip("/").replace("/", "_") + ".json"
    return GB300_INDEX.get(name.lower())


def test_fleet_inventory_reads_gb300_node_from_yaml(tmp_path):
    """fleet reads a YAML node list and returns corpus-backed status rows."""
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

    inventory = tmp_path / "fleet.yaml"
    inventory.write_text(
        yaml.safe_dump({
            "nodes": [
                {
                    "name": "gb300-a",
                    "address": "mock-gb300",
                    "username": "root",
                    "password": "mock",
                    "insecure": True,
                }
            ]
        })
    )

    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=get_cb)
        result = FleetInventory().execute(inventory=str(inventory), concurrency=2)

    assert result.error is None
    assert result.data["summary"] == {"total": 1, "ok": 1, "failed": 0}
    assert result.data["nodes"] == [
        {
            "name": "gb300-a",
            "address": "mock-gb300",
            "ok": True,
            "powerState": "On",
            "health": "OK",
            "state": "Enabled",
            "sensors": {"count": 266},
            "temperature": {"count": 72, "max_celsius": 54.1875},
            "error": None,
        }
    ]
    assert {request.method for request in requests} == {"GET"}


def test_fleet_inventory_records_per_node_failures(monkeypatch):
    """fleet keeps one failed node from aborting the full inventory read."""

    def fail_node(node):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(cmd_fleet, "read_node", fail_node)

    node = FleetNode(
        name="offline-a",
        address="offline.example.test",
        username="root",
        password="mock",
        port=443,
        insecure=True,
        use_http=False,
    )

    data = read_fleet((node,), concurrency=1)

    assert data == {
        "summary": {"total": 1, "ok": 0, "failed": 1},
        "nodes": [
            {
                "name": "offline-a",
                "address": "offline.example.test",
                "ok": False,
                "powerState": None,
                "health": None,
                "state": None,
                "sensors": {"count": 0},
                "temperature": {"count": 0, "max_celsius": None},
                "error": "connection refused",
            }
        ],
    }
