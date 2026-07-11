"""Contracts for the Kubernetes sandbox mock-BMC container."""

from __future__ import annotations

import importlib.util
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_MODULE = REPO_ROOT / "k8s" / "sandbox" / "mock_bmc_server.py"
DOCKERFILE = REPO_ROOT / "docker" / "Dockerfile.mock-bmc"
MANIFEST = REPO_ROOT / "k8s" / "sandbox" / "mock-bmc.yaml"
GRACEFUL_RESTART_TRACE = REPO_ROOT / "tests" / "write_traces" / "graceful_restart.yaml"
SUPERMICRO_RULES = REPO_ROOT / "tests" / "mutation_rules" / "supermicro_gb300.yaml"
GB300_CORPUS = (
    REPO_ROOT
    / "tests"
    / "supermicro_gb300_corpus"
    / "json_responses"
    / "172.25.230.37"
)

SYSTEM = "/redfish/v1/Systems/System_0"
RESET = f"{SYSTEM}/Actions/ComputerSystem.Reset"
BIOS = f"{SYSTEM}/Bios"
BIOS_SETTINGS = f"{BIOS}/Settings"
USB1 = "/redfish/v1/Managers/BMC_0/VirtualMedia/USB1"


def _load_server_module():
    spec = importlib.util.spec_from_file_location("mock_bmc_server", SERVER_MODULE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _http(base: str, path: str, method: str = "GET", body=None):
    """Issue an HTTP request to the mock, returning (status, decoded-json-or-None)."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    request = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            raw = response.read().decode("utf-8")
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        return exc.code, (json.loads(raw) if raw else None)


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


# --- order-independent mutation-rules mode -------------------------------------


def test_mutation_rules_power_reset_cycles_power_state() -> None:
    """A ForceOff reset powers System_0 down; an On reset brings it back."""
    module = _load_server_module()
    with module.run_server("127.0.0.1", 0, GB300_CORPUS, mutation_rules=SUPERMICRO_RULES) as srv:
        base = "http://{}:{}".format(*srv.server_address)

        _, before = _http(base, SYSTEM)
        assert before["PowerState"] == "On"

        status, _ = _http(base, RESET, "POST", {"ResetType": "ForceOff"})
        assert status == 204
        _, after = _http(base, SYSTEM)
        assert after["PowerState"] == "Off"
        assert after["Status"]["State"] == "Disabled"

        status, _ = _http(base, RESET, "POST", {"ResetType": "On"})
        assert status == 204
        _, restored = _http(base, SYSTEM)
        assert restored["PowerState"] == "On"


def test_mutation_rules_boot_override_reverts_one_time_boot_on_reset() -> None:
    """A one-time PXE override is armed, then a reset consumes it (composed with power-off)."""
    module = _load_server_module()
    with module.run_server("127.0.0.1", 0, GB300_CORPUS, mutation_rules=SUPERMICRO_RULES) as srv:
        base = "http://{}:{}".format(*srv.server_address)

        _, before = _http(base, SYSTEM)
        assert before["Boot"]["BootSourceOverrideEnabled"] == "Disabled"

        status, _ = _http(
            base, SYSTEM, "PATCH",
            {"Boot": {"BootSourceOverrideTarget": "Pxe", "BootSourceOverrideEnabled": "Once"}},
        )
        assert status == 200
        _, armed = _http(base, SYSTEM)
        assert armed["Boot"]["BootSourceOverrideTarget"] == "Pxe"
        assert armed["Boot"]["BootSourceOverrideEnabled"] == "Once"

        # One ForceOff reset fires BOTH the power-off rule and the one-time revert.
        status, _ = _http(base, RESET, "POST", {"ResetType": "ForceOff"})
        assert status == 204
        _, after = _http(base, SYSTEM)
        assert after["PowerState"] == "Off"
        assert after["Boot"]["BootSourceOverrideEnabled"] == "Disabled"
        assert after["Boot"]["BootSourceOverrideTarget"] == "None"


def test_mutation_rules_bios_pending_applies_on_reset() -> None:
    """A staged BIOS attribute stays pending until a reset moves it to the live resource."""
    module = _load_server_module()
    with module.run_server("127.0.0.1", 0, GB300_CORPUS, mutation_rules=SUPERMICRO_RULES) as srv:
        base = "http://{}:{}".format(*srv.server_address)

        _, bios_before = _http(base, BIOS)
        assert bios_before["Attributes"]["Above4GDecoding"] == "Enabled"

        status, _ = _http(
            base, BIOS_SETTINGS, "PATCH", {"Attributes": {"Above4GDecoding": "Disabled"}}
        )
        assert status == 200
        # Pending only: the live BIOS resource is unchanged until a reset.
        _, staged = _http(base, BIOS_SETTINGS)
        assert staged["Attributes"]["Above4GDecoding"] == "Disabled"
        _, bios_mid = _http(base, BIOS)
        assert bios_mid["Attributes"]["Above4GDecoding"] == "Enabled"

        status, _ = _http(base, RESET, "POST", {"ResetType": "GracefulRestart"})
        assert status == 204
        _, bios_after = _http(base, BIOS)
        assert bios_after["Attributes"]["Above4GDecoding"] == "Disabled"


def test_mutation_rules_virtual_media_insert_and_eject() -> None:
    """Virtual media inserts then ejects, matched by state precondition not order."""
    module = _load_server_module()
    with module.run_server("127.0.0.1", 0, GB300_CORPUS, mutation_rules=SUPERMICRO_RULES) as srv:
        base = "http://{}:{}".format(*srv.server_address)

        _, before = _http(base, USB1)
        assert before["Inserted"] is False

        status, _ = _http(
            base, f"{USB1}/Actions/VirtualMedia.InsertMedia", "POST",
            {"Image": "http://boot.example/iso"},
        )
        assert status == 204
        _, inserted = _http(base, USB1)
        assert inserted["Inserted"] is True
        assert inserted["ConnectedVia"] == "URI"

        status, _ = _http(base, f"{USB1}/Actions/VirtualMedia.EjectMedia", "POST", {})
        assert status == 204
        _, ejected = _http(base, USB1)
        assert ejected["Inserted"] is False


def test_mutation_rules_are_order_independent() -> None:
    """Unrelated mutations apply regardless of the order they arrive in."""
    module = _load_server_module()
    with module.run_server("127.0.0.1", 0, GB300_CORPUS, mutation_rules=SUPERMICRO_RULES) as srv:
        base = "http://{}:{}".format(*srv.server_address)

        # Insert media, THEN stage BIOS, THEN power off — none depends on the others.
        assert _http(base, f"{USB1}/Actions/VirtualMedia.InsertMedia", "POST", {"Image": "x"})[0] == 204
        assert _http(base, BIOS_SETTINGS, "PATCH", {"Attributes": {"Above4GDecoding": "Disabled"}})[0] == 200
        assert _http(base, RESET, "POST", {"ResetType": "ForceOff"})[0] == 204

        assert _http(base, USB1)[1]["Inserted"] is True
        assert _http(base, SYSTEM)[1]["PowerState"] == "Off"
        # The reset also applied the pending BIOS change (compose, any order).
        assert _http(base, BIOS)[1]["Attributes"]["Above4GDecoding"] == "Disabled"


def test_mutation_rules_reject_unmatched_write_with_409() -> None:
    """A write no rule matches is refused with 409, not silently accepted."""
    module = _load_server_module()
    with module.run_server("127.0.0.1", 0, GB300_CORPUS, mutation_rules=SUPERMICRO_RULES) as srv:
        base = "http://{}:{}".format(*srv.server_address)
        status, payload = _http(base, SYSTEM, "PATCH", {"AssetTag": "nope"})
        assert status == 409
        assert payload["error"] == "no write rule matched the request"


def test_mutation_rules_status_reports_mode_and_applied_rules() -> None:
    """The status endpoint reflects the mutation-rules mode and applied rule names."""
    module = _load_server_module()
    with module.run_server("127.0.0.1", 0, GB300_CORPUS, mutation_rules=SUPERMICRO_RULES) as srv:
        base = "http://{}:{}".format(*srv.server_address)
        _http(base, RESET, "POST", {"ResetType": "ForceOff"})
        _, status = _http(base, "/__replay_status")
        assert status["mode"] == "mutation-rules"
        assert status["vendor"] == "supermicro-gb300"
        assert "power-off-on-forceoff" in status["applied"]


def test_mutation_rules_every_transition_targets_a_real_corpus_resource() -> None:
    """Every rule's precondition/transition path resolves to a committed fixture."""
    module = _load_server_module()
    spec = yaml.safe_load(SUPERMICRO_RULES.read_text(encoding="utf-8"))
    assert spec["vendor"] == "supermicro-gb300"
    for rule in spec["rules"]:
        resource_paths = [t["path"] for t in rule.get("state_transitions", [])]
        resource_paths += [c["path"] for c in rule.get("when", [])]
        for resource_path in resource_paths:
            fixture = module.fixture_for_redfish_path(GB300_CORPUS, resource_path)
            assert fixture is not None, f"rule {rule['name']} targets missing {resource_path}"


def test_mutation_rules_glob_path_matches_a_media_family() -> None:
    """A glob rule path matches every member of a resource family (engine feature)."""
    module = _load_server_module()
    rules = module.MutationRules(
        {
            "vendor": "glob-demo",
            "rules": [
                {
                    "name": "eject-any-slot",
                    "method": "POST",
                    "path": "/redfish/v1/Managers/BMC_0/VirtualMedia/*/Actions/VirtualMedia.EjectMedia",
                    "state_transitions": [
                        {"op": "set", "path": "/redfish/v1/Managers/BMC_0/VirtualMedia/USB2",
                         "field": "Inserted", "value": False},
                    ],
                    "response": {"status": 204},
                }
            ],
        }
    )
    matched = rules.match_write(
        "POST",
        "/redfish/v1/Managers/BMC_0/VirtualMedia/USB2/Actions/VirtualMedia.EjectMedia",
        {},
        lambda _p: {},
    )
    assert matched == {"status": 204}
    assert rules.match_write("POST", "/redfish/v1/Systems/System_0", {}, lambda _p: {}) is None


def test_make_handler_rejects_replay_and_mutation_rules_together() -> None:
    """The two write modes are mutually exclusive."""
    module = _load_server_module()
    with pytest.raises(ValueError, match="not both"):
        module.make_handler(
            GB300_CORPUS,
            replay_trace=GRACEFUL_RESTART_TRACE,
            mutation_rules=SUPERMICRO_RULES,
        )
