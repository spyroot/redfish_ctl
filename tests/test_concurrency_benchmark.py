"""Smoke tests for the opt-in mock-BMC concurrency benchmark."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from tests.request_benchmark import (
    run_concurrency_benchmark,
    write_concurrency_report,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_MODULE = REPO_ROOT / "k8s" / "sandbox" / "mock_bmc_server.py"
SYSTEM = "/redfish/v1/Systems/System_0"


def _load_server_module():
    spec = importlib.util.spec_from_file_location("mock_bmc_server", SERVER_MODULE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _tiny_corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "_redfish_v1.json").write_text(
        json.dumps(
            {
                "@odata.id": "/redfish/v1",
                "Systems": {"@odata.id": "/redfish/v1/Systems"},
                "RedfishVersion": "1.17.0",
            }
        ),
        encoding="utf-8",
    )
    (corpus / "_redfish_v1_Systems_System_0.json").write_text(
        json.dumps(
            {
                "@odata.id": SYSTEM,
                "Id": "System_0",
                "Name": "Tiny System",
                "PowerState": "On",
                "Status": {"Health": "OK", "State": "Enabled"},
            }
        ),
        encoding="utf-8",
    )
    return corpus


def test_concurrency_benchmark_records_latency_and_throughput(tmp_path: Path) -> None:
    module = _load_server_module()
    with module.run_server("127.0.0.1", 0, _tiny_corpus(tmp_path)) as server:
        base_url = "http://{}:{}".format(*server.server_address)
        report = run_concurrency_benchmark(
            base_url,
            SYSTEM,
            concurrency_levels=(1, 4),
            requests_per_level=8,
            latency_ceiling_ms=1000.0,
        )

    assert report["target"]["path"] == SYSTEM
    assert report["summary"]["max_sustained_concurrency"] == 4
    assert report["summary"]["total_errors"] == 0

    samples = report["samples"]
    assert [sample["concurrency"] for sample in samples] == [1, 4]
    for sample in samples:
        assert sample["requests"] == 8
        assert sample["errors"] == 0
        assert sample["throughput_rps"] > 0
        assert sample["latency_ms"]["p50"] >= 0
        assert sample["latency_ms"]["p95"] <= 1000.0
        assert sample["latency_ms"]["p99"] <= 1000.0

    output = tmp_path / "reports" / "concurrency-benchmark.json"
    write_concurrency_report(report, output)
    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert persisted["summary"] == report["summary"]
