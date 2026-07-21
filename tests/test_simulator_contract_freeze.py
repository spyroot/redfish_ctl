"""Freeze the current Redfish simulator contract for later refactors."""

from __future__ import annotations

import importlib.util
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml
from vendor_corpus import corpus_dir

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_MODULE = REPO_ROOT / "k8s" / "sandbox" / "mock_bmc_server.py"
MOCK_DOCKERFILE = REPO_ROOT / "docker" / "Dockerfile.mock-bmc"
ILO_DOCKERFILE = REPO_ROOT / "docker" / "Dockerfile.ilo-sim"
MOCK_MANIFEST = REPO_ROOT / "k8s" / "sandbox" / "mock-bmc.yaml"
FLEET_MANIFEST = REPO_ROOT / "k8s" / "sandbox" / "gb300-fleet.yaml"
ILO_MANIFEST = REPO_ROOT / "k8s" / "sandbox" / "ilo-sim.yaml"
CONTRACT_DOC = REPO_ROOT / "docs" / "external" / "simulator-contract.md"
GB300_CORPUS = corpus_dir(
    REPO_ROOT / "tests" / "supermicro_gb300_corpus.tar.gz", "172.25.230.37"
)
SUPERMICRO_RULES = REPO_ROOT / "tests" / "mutation_rules" / "supermicro_gb300.yaml"
FLAKY_RULES = REPO_ROOT / "tests" / "mutation_rules" / "supermicro_gb300_flaky.yaml"

SYSTEM = "/redfish/v1/Systems/System_0"
RESET = f"{SYSTEM}/Actions/ComputerSystem.Reset"


def _load_server_module() -> Any:
    spec = importlib.util.spec_from_file_location("mock_bmc_server", SERVER_MODULE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _http(
    base: str,
    path: str,
    method: str = "GET",
    body: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    request = urllib.request.Request(
        base + path,
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        return exc.code, json.loads(raw) if raw else None


def _write_two_step_replay(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "scenario": "two_step_power",
                "steps": [
                    {
                        "name": "force_off_first",
                        "method": "POST",
                        "path": RESET,
                        "body": {"ResetType": "ForceOff"},
                        "response": {"status": 204},
                        "state_transitions": [
                            {
                                "op": "set",
                                "path": SYSTEM,
                                "field": "PowerState",
                                "value": "Off",
                            }
                        ],
                    },
                    {
                        "name": "power_on_second",
                        "method": "POST",
                        "path": RESET,
                        "body": {"ResetType": "On"},
                        "response": {"status": 204},
                        "state_transitions": [
                            {
                                "op": "set",
                                "path": SYSTEM,
                                "field": "PowerState",
                                "value": "On",
                            }
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def test_replay_state_is_ordered_and_resettable(tmp_path: Path) -> None:
    module = _load_server_module()
    trace = tmp_path / "two-step-replay.json"
    _write_two_step_replay(trace)

    with module.run_server("127.0.0.1", 0, GB300_CORPUS, replay_trace=trace) as srv:
        base = "http://{}:{}".format(*srv.server_address)

        assert _http(base, SYSTEM)[1]["PowerState"] == "On"

        status, payload = _http(base, RESET, "POST", {"ResetType": "On"})
        assert status == 409
        assert payload["status"]["pending_steps"] == [
            "force_off_first",
            "power_on_second",
        ]

        assert _http(base, RESET, "POST", {"ResetType": "ForceOff"})[0] == 204
        assert _http(base, SYSTEM)[1]["PowerState"] == "Off"
        assert _http(base, RESET, "POST", {"ResetType": "On"})[0] == 204
        assert _http(base, SYSTEM)[1]["PowerState"] == "On"
        assert _http(base, "/__replay_status")[1]["complete"] is True

        status, payload = _http(
            base,
            "/__set_scenario",
            "POST",
            {"scenario": "two_step_power"},
        )
        assert status == 200
        assert payload["matched_steps"] == 0
        assert payload["pending_steps"] == ["force_off_first", "power_on_second"]
        assert _http(base, SYSTEM)[1]["PowerState"] == "On"


def test_mutation_rules_compose_and_reject_unmatched_writes() -> None:
    module = _load_server_module()

    with module.run_server(
        "127.0.0.1",
        0,
        GB300_CORPUS,
        mutation_rules=SUPERMICRO_RULES,
    ) as srv:
        base = "http://{}:{}".format(*srv.server_address)

        assert _http(
            base,
            SYSTEM,
            "PATCH",
            {"Boot": {"BootSourceOverrideTarget": "Pxe", "BootSourceOverrideEnabled": "Once"}},
        )[0] == 200
        assert _http(base, RESET, "POST", {"ResetType": "ForceOff"})[0] == 204

        system = _http(base, SYSTEM)[1]
        assert system["PowerState"] == "Off"
        assert system["Boot"]["BootSourceOverrideEnabled"] == "Disabled"
        assert system["Boot"]["BootSourceOverrideTarget"] == "None"

        status, payload = _http(base, SYSTEM, "PATCH", {"AssetTag": "unsupported"})
        assert status == 409
        assert payload["error"] == "no write rule matched the request"
        assert payload["status"]["mode"] == "mutation-rules"


def test_mutation_rule_seed_reset_replays_failure_sequence() -> None:
    module = _load_server_module()

    with module.run_server(
        "127.0.0.1",
        0,
        GB300_CORPUS,
        mutation_rules=FLAKY_RULES,
        seed=1,
    ) as srv:
        base = "http://{}:{}".format(*srv.server_address)
        first_status, first_payload = _http(
            base,
            RESET,
            "POST",
            {"ResetType": "ForceOff"},
        )
        assert first_status == 503
        assert "Reset action failed" in first_payload["error"]["message"]
        assert _http(base, SYSTEM)[1]["PowerState"] == "On"

        reset_status, reset_payload = _http(
            base,
            "/__set_scenario",
            "POST",
            {"scenario": "supermicro-gb300-flaky"},
        )
        assert reset_status == 200
        assert reset_payload["failed"] == []

        second_status, second_payload = _http(
            base,
            RESET,
            "POST",
            {"ResetType": "ForceOff"},
        )
        assert second_status == first_status
        assert second_payload == first_payload


def test_runtime_entrypoints_and_control_endpoints_are_frozen() -> None:
    module = _load_server_module()
    dockerfile = MOCK_DOCKERFILE.read_text(encoding="utf-8")
    ilo_dockerfile = ILO_DOCKERFILE.read_text(encoding="utf-8")
    mock_manifest = yaml.safe_load_all(MOCK_MANIFEST.read_text(encoding="utf-8"))
    fleet_manifest = yaml.safe_load_all(FLEET_MANIFEST.read_text(encoding="utf-8"))
    ilo_manifest = yaml.safe_load_all(ILO_MANIFEST.read_text(encoding="utf-8"))

    assert 'ENTRYPOINT ["python", "/app/mock_bmc_server.py"]' in dockerfile
    assert 'CMD ["--host", "0.0.0.0", "--port", "8080"]' in dockerfile
    assert 'ENTRYPOINT ["python3", "emulator.py"]' in ilo_dockerfile
    assert any(doc["kind"] == "Deployment" for doc in mock_manifest if doc)
    assert any(doc["kind"] == "StatefulSet" for doc in fleet_manifest if doc)
    assert any(doc["kind"] == "Deployment" for doc in ilo_manifest if doc)

    with module.run_server(
        "127.0.0.1",
        0,
        GB300_CORPUS,
        mutation_rules=SUPERMICRO_RULES,
    ) as srv:
        base = "http://{}:{}".format(*srv.server_address)
        assert _http(base, "/__replay_status")[1]["mode"] == "mutation-rules"
        assert _http(base, "/__set_scenario", "POST", {"scenario": "supermicro-gb300"})[0] == 200


def test_contract_doc_records_current_rule_matrix() -> None:
    text = CONTRACT_DOC.read_text(encoding="utf-8")

    for expected in [
        "`k8s/sandbox/mock_bmc_server.py`",
        "`ReplayState`",
        "`MutationRules`",
        "`tests/write_traces/graceful_restart.yaml`",
        "`tests/mutation_rules/supermicro_gb300.yaml`",
        "| Dell XR8620t | supported | supported | supported | unsupported | supported | supported |",
        "| Supermicro GB300 | supported | supported | supported | supported | unsupported | unsupported |",
        "| HPE DL360 | supported | supported | supported | unsupported | supported | unsupported |",
        "| Supermicro X10 | supported | supported | unsupported | unsupported | supported | unsupported |",
        "| NVIDIA GB300 node2 | supported | supported | supported | supported | supported | unsupported |",
        "## Captured Error Replay Matrix",
        "| Supermicro X10 | `full_corpus/supermicro_x10_full_corpus.tar.gz` records four captured `403` responses | supported |",
        "| Dell XR8620t | `full_corpus/dell_xr8620t_full_corpus.tar.gz` has no captured non-2xx mappings | unverified |",
    ]:
        assert expected in text
