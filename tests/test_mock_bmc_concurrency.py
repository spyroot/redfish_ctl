"""Concurrency contracts for the sandbox mock-BMC server."""

from __future__ import annotations

import importlib.util
import json
import time
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_MODULE = REPO_ROOT / "k8s" / "sandbox" / "mock_bmc_server.py"

SYSTEM = "/redfish/v1/Systems/System_0"
RESET = f"{SYSTEM}/Actions/ComputerSystem.Reset"
NETWORK_PROTOCOL = "/redfish/v1/Managers/BMC_0/NetworkProtocol"
CHASSIS = "/redfish/v1/Chassis/Chassis_0"

MAX_CONCURRENT_SECONDS = 20.0


def _load_server_module() -> Any:
    spec = importlib.util.spec_from_file_location("mock_bmc_server", SERVER_MODULE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _fixture_name(path: str) -> str:
    return "_" + path.strip("/").replace("/", "_") + ".json"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _tiny_corpus(tmp_path: Path) -> tuple[Path, dict[str, dict[str, Any]]]:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    payloads = {
        "/redfish/v1": {
            "@odata.id": "/redfish/v1",
            "Id": "RootService",
            "Systems": {"@odata.id": "/redfish/v1/Systems"},
        },
        SYSTEM: {
            "@odata.id": SYSTEM,
            "Id": "System_0",
            "Name": "Concurrency test node",
            "PowerState": "On",
            "LastResetTime": "2026-07-12T00:00:00Z",
            "Boot": {
                "BootSourceOverrideEnabled": "Once",
                "BootSourceOverrideTarget": "Pxe",
            },
            "Status": {"Health": "OK", "State": "Enabled"},
        },
        NETWORK_PROTOCOL: {
            "@odata.id": NETWORK_PROTOCOL,
            "Id": "NetworkProtocol",
            "NTP": {"ProtocolEnabled": True, "NTPServers": ["time.example.test"]},
        },
        CHASSIS: {
            "@odata.id": CHASSIS,
            "Id": "Chassis_0",
            "Name": "Concurrency chassis",
            "Status": {"Health": "OK", "State": "Enabled"},
        },
    }
    for redfish_path, payload in payloads.items():
        _write_json(corpus / _fixture_name(redfish_path), payload)
    return corpus, payloads


def _mutation_rules(tmp_path: Path) -> Path:
    rules = {
        "vendor": "concurrency-test",
        "rules": [
            {
                "name": "record-graceful-restart",
                "method": "POST",
                "path": RESET,
                "body_contains": {"ResetType": "GracefulRestart"},
                "state_transitions": [
                    {
                        "op": "set",
                        "path": SYSTEM,
                        "field": "LastResetTime",
                        "value": "2099-01-01T00:00:00Z",
                    }
                ],
                "response": {"status": 204},
            },
            {
                "name": "disable-boot-override",
                "method": "PATCH",
                "path": SYSTEM,
                "body_contains": {"Boot": {"BootSourceOverrideEnabled": "Disabled"}},
                "state_transitions": [
                    {
                        "op": "set",
                        "path": SYSTEM,
                        "json_path": ["Boot", "BootSourceOverrideEnabled"],
                        "value": "Disabled",
                    },
                    {
                        "op": "set",
                        "path": SYSTEM,
                        "json_path": ["Boot", "BootSourceOverrideTarget"],
                        "value": "None",
                    },
                ],
                "response": {"status": 200, "body": {"accepted": True}},
            },
        ],
    }
    rules_path = tmp_path / "mutation-rules.json"
    _write_json(rules_path, rules)
    return rules_path


def _request(base: str, path: str, method: str = "GET", body: dict[str, Any] | None = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    request = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=5) as response:
        raw = response.read().decode("utf-8")
        payload = json.loads(raw) if raw else None
        return response.status, payload


def test_mock_bmc_serves_many_concurrent_reads(tmp_path: Path) -> None:
    """Concurrent GETs return complete, path-correct JSON bodies."""
    module = _load_server_module()
    corpus, expected_payloads = _tiny_corpus(tmp_path)
    paths = list(expected_payloads)

    with module.run_server("127.0.0.1", 0, corpus) as server:
        base = "http://{}:{}".format(*server.server_address)
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = [
                pool.submit(_request, base, paths[index % len(paths)])
                for index in range(64)
            ]
            results = [future.result() for future in as_completed(futures)]
        elapsed = time.perf_counter() - started

    assert elapsed < MAX_CONCURRENT_SECONDS
    assert len(results) == 64
    for status, payload in results:
        assert status == 200
        assert payload == expected_payloads[payload["@odata.id"]]


def test_mutation_rules_serialize_concurrent_writes_with_reads(tmp_path: Path) -> None:
    """Concurrent writes are counted once each while reads keep returning valid JSON."""
    module = _load_server_module()
    corpus, _ = _tiny_corpus(tmp_path)
    rules = _mutation_rules(tmp_path)

    with module.run_server("127.0.0.1", 0, corpus, mutation_rules=rules) as server:
        base = "http://{}:{}".format(*server.server_address)
        jobs = []
        for _ in range(16):
            jobs.append((RESET, "POST", {"ResetType": "GracefulRestart"}))
            jobs.append((SYSTEM, "PATCH", {"Boot": {"BootSourceOverrideEnabled": "Disabled"}}))
            jobs.append((SYSTEM, "GET", None))
            jobs.append((NETWORK_PROTOCOL, "GET", None))

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = [
                pool.submit(_request, base, path, method, body)
                for path, method, body in jobs
            ]
            results = [future.result() for future in as_completed(futures)]
        elapsed = time.perf_counter() - started

        system_status, system = _request(base, SYSTEM)
        replay_status, replay = _request(base, "/__replay_status")

    assert elapsed < MAX_CONCURRENT_SECONDS
    assert len(results) == len(jobs)
    assert all(status in {200, 204} for status, _payload in results)
    assert system_status == 200
    assert system["LastResetTime"] == "2099-01-01T00:00:00Z"
    assert system["Boot"]["BootSourceOverrideEnabled"] == "Disabled"
    assert system["Boot"]["BootSourceOverrideTarget"] == "None"
    assert replay_status == 200
    assert Counter(replay["applied"]) == {
        "record-graceful-restart": 16,
        "disable-boot-override": 16,
    }
    assert replay["failed"] == []
