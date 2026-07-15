"""Contracts for the Kubernetes sandbox mock-BMC container."""

from __future__ import annotations

import importlib.util
import json
import logging
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pytest
import yaml
from vendor_corpus import corpus_dir

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_MODULE = REPO_ROOT / "k8s" / "sandbox" / "mock_bmc_server.py"
DOCKERFILE = REPO_ROOT / "docker" / "Dockerfile.mock-bmc"
MANIFEST = REPO_ROOT / "k8s" / "sandbox" / "mock-bmc.yaml"
FLEET_MANIFEST = REPO_ROOT / "k8s" / "sandbox" / "gb300-fleet.yaml"
HELM_VALUES = REPO_ROOT / "charts" / "redfish-controller" / "values.yaml"
SIMULATION_DOC = REPO_ROOT / "docs" / "simulation-and-replay.md"
GRACEFUL_RESTART_TRACE = REPO_ROOT / "tests" / "write_traces" / "graceful_restart.yaml"
SUPERMICRO_RULES = REPO_ROOT / "tests" / "mutation_rules" / "supermicro_gb300.yaml"
STATUS_SIDECAR = "rest_api_map.status.json"
GB300_CORPUS = corpus_dir(
    REPO_ROOT / "tests" / "supermicro_gb300_corpus.tar.gz", "172.25.230.37"
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


def _fixture_name(path: str) -> str:
    return "_" + path.strip("/").replace("/", "_") + ".json"


def _write_status_map_corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "status-map-corpus"
    corpus.mkdir()
    normal_path = "/redfish/v1/Systems/1"
    error_cases = {
        "/redfish/v1/Secret": (
            403,
            "_redfish_v1_Secret.error.json",
            {"error": {"code": "Base.1.17.InsufficientPrivilege"}},
        ),
        "/redfish/v1/Missing": (
            404,
            "_redfish_v1_Missing.error.json",
            {"error": {"code": "Base.1.17.ResourceMissingAtURI"}},
        ),
        "/redfish/v1/ReadOnly": (
            405,
            "_redfish_v1_ReadOnly.error.json",
            {"error": {"code": "Base.1.17.ActionNotSupported"}},
        ),
    }

    (corpus / _fixture_name("/redfish/v1")).write_text(
        json.dumps({"@odata.id": "/redfish/v1/"}),
        encoding="utf-8",
    )
    (corpus / _fixture_name(normal_path)).write_text(
        json.dumps({"@odata.id": normal_path}),
        encoding="utf-8",
    )
    for _path, (_status, filename, payload) in error_cases.items():
        (corpus / filename).write_text(json.dumps(payload), encoding="utf-8")

    np.save(
        corpus / "rest_api_map.npy",
        {
            "url_file_mapping": {
                "/redfish/v1/": _fixture_name("/redfish/v1"),
                normal_path: _fixture_name(normal_path),
            },
            "http_status_mapping": {
                "/redfish/v1/": 200,
                normal_path: 200,
                **{path: status for path, (status, _name, _body) in error_cases.items()},
            },
            "error_file_mapping": {
                path: filename for path, (_status, filename, _body) in error_cases.items()
            },
        },
    )
    return corpus


def _write_status_sidecar_corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "status-sidecar-corpus"
    corpus.mkdir()
    error_path = "/redfish/v1/Missing"
    error_file = "_redfish_v1_Missing.error.json"

    (corpus / _fixture_name("/redfish/v1")).write_text(
        json.dumps({"@odata.id": "/redfish/v1/"}),
        encoding="utf-8",
    )
    (corpus / error_file).write_text(
        json.dumps({"error": {"code": "Base.1.17.CapturedMissing"}}),
        encoding="utf-8",
    )
    (corpus / STATUS_SIDECAR).write_text(
        json.dumps(
            {
                "http_status_mapping": {error_path: 404},
                "error_file_mapping": {error_path: error_file},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return corpus


def _write_url_map_corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "url-map-corpus"
    corpus.mkdir()
    mapped_dir = corpus / "mapped"
    mapped_dir.mkdir()

    (corpus / _fixture_name("/redfish/v1")).write_text(
        json.dumps({"@odata.id": "/redfish/v1/"}),
        encoding="utf-8",
    )
    (mapped_dir / "aliased-system.json").write_text(
        json.dumps(
            {
                "@odata.id": "/redfish/v1/Systems/Original",
                "Id": "Original",
            }
        ),
        encoding="utf-8",
    )
    np.save(
        corpus / "rest_api_map.npy",
        {
            "url_file_mapping": {
                "/redfish/v1/": _fixture_name("/redfish/v1"),
                "/redfish/v1/Alias": "mapped/aliased-system.json",
            },
            "http_status_mapping": {
                "/redfish/v1/": 200,
                "/redfish/v1/Alias": 200,
            },
            "error_file_mapping": {},
        },
    )
    return corpus


def _write_sidecar_with_legacy_alias_corpus(tmp_path: Path) -> Path:
    corpus = _write_url_map_corpus(tmp_path)
    error_path = "/redfish/v1/Missing"
    error_file = "_redfish_v1_Missing.error.json"

    (corpus / error_file).write_text(
        json.dumps({"error": {"code": "Base.1.17.CapturedMissing"}}),
        encoding="utf-8",
    )
    (corpus / STATUS_SIDECAR).write_text(
        json.dumps(
            {
                "http_status_mapping": {error_path: 404},
                "error_file_mapping": {error_path: error_file},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return corpus


def _write_sidecar_with_legacy_status_conflict_corpus(tmp_path: Path) -> Path:
    corpus = _write_sidecar_with_legacy_alias_corpus(tmp_path)
    error_path = "/redfish/v1/Missing"
    api_map = np.load(corpus / "rest_api_map.npy", allow_pickle=True).item()
    api_map["http_status_mapping"][error_path] = 200
    api_map["error_file_mapping"][error_path] = "_legacy_missing.error.json"
    np.save(corpus / "rest_api_map.npy", api_map)
    return corpus


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


@pytest.mark.parametrize(
    ("path", "status", "code"),
    [
        ("/redfish/v1/Secret", 403, "Base.1.17.InsufficientPrivilege"),
        ("/redfish/v1/Missing", 404, "Base.1.17.ResourceMissingAtURI"),
        ("/redfish/v1/ReadOnly", 405, "Base.1.17.ActionNotSupported"),
    ],
)
def test_mock_bmc_replays_captured_error_status_map(
    tmp_path: Path,
    path: str,
    status: int,
    code: str,
) -> None:
    module = _load_server_module()
    corpus = _write_status_map_corpus(tmp_path)

    with module.run_server("127.0.0.1", 0, corpus) as server:
        base = "http://{}:{}".format(*server.server_address)

        assert _http(base, "/redfish/v1/Systems/1")[0] == 200
        observed_status, payload = _http(base, path)

    assert observed_status == status
    assert payload == {"error": {"code": code}}


def test_mock_bmc_replays_captured_error_status_json_sidecar(
    tmp_path: Path,
) -> None:
    """Captured errors replay from JSON sidecar without requiring rest_api_map.npy."""
    module = _load_server_module()
    corpus = _write_status_sidecar_corpus(tmp_path)

    with module.run_server("127.0.0.1", 0, corpus) as server:
        base = "http://{}:{}".format(*server.server_address)
        observed_status, payload = _http(base, "/redfish/v1/Missing")

    assert observed_status == 404
    assert payload == {"error": {"code": "Base.1.17.CapturedMissing"}}


def test_mock_bmc_serves_url_file_mapping_alias(tmp_path: Path) -> None:
    """URL mappings can serve fixtures whose names do not derive from the URL."""
    module = _load_server_module()
    corpus = _write_url_map_corpus(tmp_path)

    with module.run_server("127.0.0.1", 0, corpus) as server:
        base = "http://{}:{}".format(*server.server_address)
        status, payload = _http(base, "/redfish/v1/Alias")

    assert status == 200
    assert payload == {
        "@odata.id": "/redfish/v1/Systems/Original",
        "Id": "Original",
    }


def test_status_json_sidecar_preserves_legacy_url_file_mapping_alias(
    tmp_path: Path,
) -> None:
    """A status sidecar must not shadow non-derived URL aliases from the npy map."""
    module = _load_server_module()
    corpus = _write_sidecar_with_legacy_alias_corpus(tmp_path)

    with module.run_server("127.0.0.1", 0, corpus) as server:
        base = "http://{}:{}".format(*server.server_address)
        alias_status, alias_payload = _http(base, "/redfish/v1/Alias")
        error_status, error_payload = _http(base, "/redfish/v1/Missing")

    assert alias_status == 200
    assert alias_payload == {
        "@odata.id": "/redfish/v1/Systems/Original",
        "Id": "Original",
    }
    assert error_status == 404
    assert error_payload == {"error": {"code": "Base.1.17.CapturedMissing"}}


def test_status_json_sidecar_warns_when_overriding_legacy_status(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Sidecar overrides of legacy replay entries are visible in logs."""
    module = _load_server_module()
    corpus = _write_sidecar_with_legacy_status_conflict_corpus(tmp_path)
    caplog.set_level(logging.WARNING, logger="mock_bmc_server")

    with module.run_server("127.0.0.1", 0, corpus) as server:
        base = "http://{}:{}".format(*server.server_address)
        status, payload = _http(base, "/redfish/v1/Missing")

    assert status == 404
    assert payload == {"error": {"code": "Base.1.17.CapturedMissing"}}
    assert (
        "rest_api_map.status.json overrides http_status_mapping "
        "for /redfish/v1/Missing"
    ) in caplog.text


def test_rest_api_map_rejects_malformed_status_values(tmp_path: Path) -> None:
    """A bad status map fails startup instead of silently dropping a status."""
    module = _load_server_module()
    corpus = _write_status_map_corpus(tmp_path)
    np.save(
        corpus / "rest_api_map.npy",
        {
            "url_file_mapping": {"/redfish/v1/": _fixture_name("/redfish/v1")},
            "http_status_mapping": {"/redfish/v1/Broken": "not-a-status"},
            "error_file_mapping": {},
        },
    )

    with pytest.raises(ValueError, match="http_status_mapping"):
        module.make_handler(corpus)


def test_rest_api_map_rejects_incomplete_status_json_sidecar(tmp_path: Path) -> None:
    """A sidecar must not shadow a usable legacy map without both replay maps."""
    module = _load_server_module()
    corpus = _write_status_sidecar_corpus(tmp_path)
    (corpus / STATUS_SIDECAR).write_text(
        json.dumps({"http_status_mapping": {"/redfish/v1/Missing": 404}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="error_file_mapping"):
        module.make_handler(corpus)


def test_rest_api_map_rejects_incomplete_status_json_sidecar_with_legacy_map(
    tmp_path: Path,
) -> None:
    """A partial sidecar fails closed even when a legacy map is present."""
    module = _load_server_module()
    corpus = _write_sidecar_with_legacy_alias_corpus(tmp_path)
    (corpus / STATUS_SIDECAR).write_text(
        json.dumps({"http_status_mapping": {"/redfish/v1/Missing": 404}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="error_file_mapping"):
        module.make_handler(corpus)


def test_rest_api_map_rejects_malformed_status_json_sidecar(tmp_path: Path) -> None:
    """A malformed sidecar fails closed instead of falling back silently."""
    module = _load_server_module()
    corpus = _write_status_sidecar_corpus(tmp_path)
    (corpus / STATUS_SIDECAR).write_text("{not-json", encoding="utf-8")

    with pytest.raises(ValueError, match="failed to load Redfish API map"):
        module.make_handler(corpus)


def test_absolute_mapped_fixture_names_are_ignored(tmp_path: Path) -> None:
    """Mapped fixture names stay corpus-relative instead of accepting paths."""
    module = _load_server_module()
    corpus = _write_status_map_corpus(tmp_path)

    assert module._resolve_mapped_fixture(
        corpus,
        "/tmp/_redfish_v1_Missing.error.json",
    ) is None


def test_mapped_fixture_names_cannot_escape_corpus_dir(tmp_path: Path) -> None:
    """A rest_api_map entry with '..' segments must never resolve outside the
    corpus dir. A real secret file is planted next to (not inside) the corpus;
    a crafted ``../`` mapping must not serve it (path-traversal guard)."""
    module = _load_server_module()
    corpus = _write_status_map_corpus(tmp_path)
    secret = tmp_path / "secret.json"
    secret.write_text('{"leaked": true}', encoding="utf-8")

    # relative traversal that would land on the planted secret one level up
    assert module._resolve_mapped_fixture(corpus, "../secret.json") is None
    assert module._resolve_mapped_fixture(corpus, "../../etc/hostname") is None
    # a legitimate in-corpus name still resolves
    good = next(corpus.glob("*.json"))
    assert module._resolve_mapped_fixture(corpus, good.name) == good.resolve()


def test_head_to_captured_error_status_suppresses_body(tmp_path: Path) -> None:
    """HEAD returns captured error headers without writing an error body."""
    module = _load_server_module()
    corpus = _write_status_map_corpus(tmp_path)

    with module.run_server("127.0.0.1", 0, corpus) as server:
        base = "http://{}:{}".format(*server.server_address)
        request = urllib.request.Request(
            base + "/redfish/v1/Missing",
            method="HEAD",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(request, timeout=5)

    assert exc_info.value.code == 404
    assert exc_info.value.read() == b""
    assert int(exc_info.value.headers["Content-Length"]) > 0


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
    assert "numpy" in dockerfile
    assert "--chown=mockbmc:mockbmc" in dockerfile
    assert "k8s/sandbox/mock_bmc_server.py" in dockerfile
    assert "tests/supermicro_gb300_corpus.tar.gz" in dockerfile
    assert "MOCK_BMC_CORPUS_DIR=/corpus/gb300" in dockerfile
    assert "/corpus/gb300" in dockerfile
    assert "/corpus/172.25.230.37" not in dockerfile
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
    assert container["env"] == [
        {
            "name": "MOCK_BMC_CORPUS_DIR",
            "value": "/corpus/gb300",
        }
    ]
    assert container["readinessProbe"]["httpGet"]["path"] == "/redfish/v1/"
    assert service["spec"]["ports"][0]["targetPort"] == "http"
    assert not {name for name in env_names if "PASSWORD" in name or "SECRET" in name}


def test_public_runtime_paths_do_not_embed_lab_addresses() -> None:
    """Public image, manifest, chart, and doc paths use a neutral corpus mount."""
    public_files = [
        DOCKERFILE,
        SERVER_MODULE,
        MANIFEST,
        FLEET_MANIFEST,
        HELM_VALUES,
        SIMULATION_DOC,
    ]

    for path in public_files:
        text = path.read_text(encoding="utf-8")
        assert "/corpus/172.25.230.37" not in text, path
        assert "/corpus/gb300" in text, path


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


# --- stochastic failure injection (RL) -----------------------------------------

FLAKY_RULES = REPO_ROOT / "tests" / "mutation_rules" / "supermicro_gb300_flaky.yaml"


def _prob_engine(module, seed: int, probability: float):
    """A one-rule engine whose write always matches and fails with `probability`."""
    return module.MutationRules(
        {
            "vendor": "t",
            "rules": [
                {
                    "name": "flaky",
                    "method": "POST",
                    "path": "/x",
                    "failure": {"probability": probability, "response": {"status": 503}},
                    "state_transitions": [
                        {"op": "set", "path": "/x", "field": "n", "value": 1}
                    ],
                    "response": {"status": 204},
                }
            ],
        },
        seed=seed,
    )


def _sequence(engine, n: int) -> list[int]:
    return [engine.match_write("POST", "/x", {}, lambda _p: {})["status"] for _ in range(n)]


def test_failure_injection_is_reproducible_and_seed_dependent() -> None:
    """The same seed replays the same failure sequence; a different seed differs."""
    module = _load_server_module()
    assert _sequence(_prob_engine(module, 0, 0.5), 40) == _sequence(
        _prob_engine(module, 0, 0.5), 40
    )
    assert _sequence(_prob_engine(module, 0, 0.5), 40) != _sequence(
        _prob_engine(module, 1, 0.5), 40
    )


def test_probability_one_always_fails_and_never_mutates_state() -> None:
    """A certain failure returns the error and applies no state transition."""
    module = _load_server_module()
    engine = _prob_engine(module, 0, 1.0)
    assert _sequence(engine, 5) == [503, 503, 503, 503, 503]
    assert engine.overlay_for("/x") == {}  # no transition ever applied
    assert engine.status()["failed"] == ["flaky"] * 5


def test_zero_probability_and_missing_failure_block_stay_deterministic() -> None:
    """Rules without an active failure block never touch the RNG or fail."""
    module = _load_server_module()
    assert _sequence(_prob_engine(module, 7, 0.0), 5) == [204, 204, 204, 204, 204]
    # The committed GB300 rules carry no failure block, so any seed is deterministic.
    rules = module.MutationRules.from_file(SUPERMICRO_RULES, seed=99)
    resp = rules.match_write(
        "POST",
        "/redfish/v1/Systems/System_0/Actions/ComputerSystem.Reset",
        {"ResetType": "ForceOff"},
        lambda _p: {"PowerState": "On"},
    )
    assert resp == {"status": 204}
    assert rules.status()["failed"] == []


def test_reset_replays_the_same_failure_sequence() -> None:
    """Scenario reset re-seeds the RNG so an episode replays identically."""
    module = _load_server_module()
    engine = _prob_engine(module, 3, 0.5)
    first = _sequence(engine, 20)
    engine.reset()
    assert _sequence(engine, 20) == first


def test_flaky_reboot_fails_over_http_without_changing_power_state() -> None:
    """With a seed that fails the first roll, a reset is rejected and power holds."""
    module = _load_server_module()
    # random.Random(1).random() ~= 0.134 < 0.3, so the first ForceOff reset fails.
    with module.run_server(
        "127.0.0.1", 0, GB300_CORPUS, mutation_rules=FLAKY_RULES, seed=1
    ) as srv:
        base = "http://{}:{}".format(*srv.server_address)
        assert _http(base, SYSTEM)[1]["PowerState"] == "On"

        status, payload = _http(base, RESET, "POST", {"ResetType": "ForceOff"})
        assert status == 503
        assert payload["error"]["message"].startswith("Reset action failed")
        # A failed reboot leaves the system exactly as it was.
        assert _http(base, SYSTEM)[1]["PowerState"] == "On"

        _, replay_status = _http(base, "/__replay_status")
        assert replay_status["seed"] == 1
        assert "power-off-on-forceoff" in replay_status["failed"]
