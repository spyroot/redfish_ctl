"""Contracts for the Kubernetes sandbox mock-BMC container."""

from __future__ import annotations

import importlib.util
import json
import urllib.error
import urllib.request
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_MODULE = REPO_ROOT / "k8s" / "sandbox" / "mock_bmc_server.py"
DOCKERFILE = REPO_ROOT / "docker" / "Dockerfile.mock-bmc"
MANIFEST = REPO_ROOT / "k8s" / "sandbox" / "mock-bmc.yaml"
GRACEFUL_RESTART_TRACE = REPO_ROOT / "tests" / "write_traces" / "graceful_restart.yaml"
GB300_CORPUS = (
    REPO_ROOT
    / "tests"
    / "supermicro_gb300_corpus"
    / "json_responses"
    / "172.25.230.37"
)


def _load_server_module():
    spec = importlib.util.spec_from_file_location("mock_bmc_server", SERVER_MODULE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_mock_bmc_maps_redfish_paths_to_gb300_corpus() -> None:
    """Redfish URLs resolve to the flattened files in the GB300 corpus."""
    module = _load_server_module()

    fixture = module.fixture_for_redfish_path(
        GB300_CORPUS,
        "/redfish/v1/Managers/BMC_0/NetworkProtocol?$select=NTP",
    )

    assert fixture == GB300_CORPUS / "_redfish_v1_Managers_BMC_0_NetworkProtocol.json"
    assert module.fixture_for_redfish_path(GB300_CORPUS, "/redfish/v1/NoSuchResource") is None


def test_mock_bmc_serves_json_read_only_over_http() -> None:
    """The HTTP server serves corpus JSON and rejects mutating verbs."""
    module = _load_server_module()

    with module.run_server("127.0.0.1", 0, GB300_CORPUS) as server:
        host, port = server.server_address
        url = f"http://{host}:{port}/redfish/v1/Managers/BMC_0/NetworkProtocol"

        with urllib.request.urlopen(url, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))

        assert payload["@odata.id"] == "/redfish/v1/Managers/BMC_0/NetworkProtocol"

        head_request = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(head_request, timeout=5) as response:
            assert response.status == 200
            assert int(response.headers["Content-Length"]) > 0

        post_request = urllib.request.Request(url, data=b"{}", method="POST")
        try:
            urllib.request.urlopen(post_request, timeout=5)
        except urllib.error.HTTPError as exc:
            assert exc.code == 405
        else:  # pragma: no cover - the assertion above is the expected path.
            raise AssertionError("POST unexpectedly succeeded")


def test_mock_bmc_replay_graceful_restart_updates_system_state() -> None:
    """Replay mode accepts the reset trace and mutates served system state."""
    module = _load_server_module()
    system_url = "/redfish/v1/Systems/System_0"
    reset_url = f"{system_url}/Actions/ComputerSystem.Reset"

    with module.run_server(
        "127.0.0.1",
        0,
        GB300_CORPUS,
        replay_trace=GRACEFUL_RESTART_TRACE,
    ) as server:
        host, port = server.server_address
        base_url = f"http://{host}:{port}"

        with urllib.request.urlopen(base_url + system_url, timeout=5) as response:
            before = json.loads(response.read().decode("utf-8"))
        assert before["LastResetTime"] == "2026-04-14T23:34:33+00:00"

        request = urllib.request.Request(
            base_url + reset_url,
            data=json.dumps({"ResetType": "GracefulRestart"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            assert response.status == 204

        with urllib.request.urlopen(base_url + system_url, timeout=5) as response:
            after = json.loads(response.read().decode("utf-8"))
        assert after["LastResetTime"] == "2026-04-14T23:35:33+00:00"

        with urllib.request.urlopen(base_url + "/__replay_status", timeout=5) as response:
            status = json.loads(response.read().decode("utf-8"))
        assert status == {
            "scenario": "graceful_restart",
            "matched_steps": 1,
            "pending_steps": [],
            "total_steps": 1,
            "complete": True,
        }


def test_mock_bmc_container_builds_from_corpus_without_credentials() -> None:
    """The sandbox image copies only the server and public corpus data."""
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "FROM python:3.12-slim" in dockerfile
    assert "--chown=mockbmc:mockbmc" in dockerfile
    assert "k8s/sandbox/mock_bmc_server.py" in dockerfile
    assert "tests/supermicro_gb300_corpus/json_responses/172.25.230.37" in dockerfile
    assert "USER mockbmc" in dockerfile
    assert "EXPOSE 8080" in dockerfile
    assert "ENTRYPOINT" in dockerfile
    assert "REDFISH_PASSWORD" not in dockerfile
    assert "IDRAC_PASSWORD" not in dockerfile


def test_mock_bmc_manifest_exposes_read_only_service_without_secrets() -> None:
    """The sandbox manifest deploys the mock BMC as an in-cluster HTTP service."""
    docs = [
        doc
        for doc in yaml.safe_load_all(MANIFEST.read_text(encoding="utf-8"))
        if doc
    ]
    by_kind = {doc["kind"]: doc for doc in docs}

    deployment = by_kind["Deployment"]
    service = by_kind["Service"]
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    env_names = {entry["name"] for entry in container.get("env", [])}

    assert deployment["metadata"]["name"] == "mock-bmc"
    assert service["metadata"]["name"] == "mock-bmc"
    assert container["image"] == "redfish-ctl-mock-bmc:local"
    assert container["ports"][0]["containerPort"] == 8080
    assert container["readinessProbe"]["httpGet"]["path"] == "/redfish/v1/"
    assert service["spec"]["ports"][0]["targetPort"] == "http"
    assert not {name for name in env_names if "PASSWORD" in name or "SECRET" in name}
