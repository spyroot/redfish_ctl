"""Helpers for request-count and concurrency benchmarks over mocked Redfish services."""

from __future__ import annotations

import argparse
import concurrent.futures
import importlib.util
import json
import math
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from redfish_ctl.idrac_shared import ApiRequestType

RTT_PROFILES = {
    "rack-local": 0.002,
    "office-vpn": 0.080,
    "india-vpn-to-us": 0.300,
    "congested-vpn": 0.800,
}
WRITE_METHODS = {"DELETE", "PATCH", "POST"}


def recorded_requests(
    service: Any,
    *,
    method: str | None = None,
    path: str | None = None,
    start: int = 0,
) -> list[Any]:
    """Return recorded requests, optionally filtered by method and path."""
    method_name = method.upper() if method else None
    expected_path = path.rstrip("/").lower() if path else None
    rows = []
    for request in service.requests[start:]:
        if method_name and request.method != method_name:
            continue
        if expected_path and request.path.rstrip("/").lower() != expected_path:
            continue
        rows.append(request)
    return rows


def projected_walltime(request_count: int, profile: str) -> float:
    """Serial wall-time projection for a request count and latency profile."""
    return request_count * RTT_PROFILES[profile]


def assert_read_budget(
    manager: Any,
    service: Any,
    *,
    api_call: ApiRequestType,
    name: str,
    max_requests: int,
    max_india_vpn_seconds: float,
    **kwargs: Any,
) -> Any:
    """Run one command and assert its request count stays under budget."""
    start = len(service.requests)
    result = manager.sync_invoke(api_call, name, **kwargs)
    requests = service.requests[start:]
    writes = [request for request in requests if request.method in WRITE_METHODS]
    assert not writes, (
        f"{name} benchmark expected a read-only path, "
        f"but saw write methods {[request.method for request in writes]}"
    )

    request_count = len(requests)
    assert request_count <= max_requests, (
        f"{name} used {request_count} BMC round trips; budget is {max_requests}. "
        f"At 300ms RTT that projects to "
        f"{projected_walltime(request_count, 'india-vpn-to-us'):.1f}s."
    )
    assert (
        projected_walltime(request_count, "india-vpn-to-us")
        <= max_india_vpn_seconds
    )
    return result


def _percentile(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = math.ceil((percentile / 100) * len(ordered)) - 1
    return round(ordered[min(max(index, 0), len(ordered) - 1)], 3)


def _target_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _fetch_once(url: str, timeout: float) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            raw = response.read()
            status = response.status
        json.loads(raw.decode("utf-8") if raw else "{}")
        error = None if status == 200 else f"HTTP {status}"
    except (OSError, ValueError, urllib.error.HTTPError) as exc:
        status = getattr(exc, "code", None)
        error = type(exc).__name__
    latency_ms = (time.perf_counter() - started) * 1000
    return {"error": error, "latency_ms": latency_ms, "status": status}


def _status_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        status = result["status"]
        key = str(status if status is not None else "error")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _error_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        error = result["error"]
        if error is None:
            continue
        counts[str(error)] = counts.get(str(error), 0) + 1
    return dict(sorted(counts.items()))


def run_concurrency_benchmark(
    base_url: str,
    path: str,
    *,
    concurrency_levels: tuple[int, ...] = (1, 8, 32, 128),
    requests_per_level: int = 128,
    timeout_seconds: float = 5.0,
    latency_ceiling_ms: float | None = 5000.0,
) -> dict[str, Any]:
    """Hammer one Redfish path and return throughput and latency percentiles."""
    if not concurrency_levels:
        raise ValueError("at least one concurrency level is required")
    if any(level < 1 for level in concurrency_levels):
        raise ValueError("concurrency levels must be positive")
    if requests_per_level < 1:
        raise ValueError("requests_per_level must be positive")

    url = _target_url(base_url, path)
    samples = []
    for level in concurrency_levels:
        request_count = max(requests_per_level, level)
        started = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=level) as pool:
            results = list(
                pool.map(lambda _: _fetch_once(url, timeout_seconds), range(request_count))
            )
        duration = max(time.perf_counter() - started, 0.000001)
        ok_latencies = [
            result["latency_ms"]
            for result in results
            if result["error"] is None and result["status"] == 200
        ]
        errors = request_count - len(ok_latencies)
        latency = {
            "min": round(min(ok_latencies), 3) if ok_latencies else None,
            "p50": round(statistics.median(ok_latencies), 3) if ok_latencies else None,
            "p95": _percentile(ok_latencies, 95),
            "p99": _percentile(ok_latencies, 99),
            "max": round(max(ok_latencies), 3) if ok_latencies else None,
        }
        samples.append(
            {
                "concurrency": level,
                "requests": request_count,
                "ok": len(ok_latencies),
                "errors": errors,
                "duration_seconds": round(duration, 3),
                "throughput_rps": round(request_count / duration, 3),
                "latency_ms": latency,
                "status_counts": _status_counts(results),
                "error_counts": _error_counts(results),
            }
        )

    max_sustained = 0
    for sample in samples:
        p99 = sample["latency_ms"]["p99"]
        within_latency = latency_ceiling_ms is None or (
            p99 is not None and p99 <= latency_ceiling_ms
        )
        if sample["errors"] == 0 and within_latency:
            max_sustained = sample["concurrency"]

    total_requests = sum(sample["requests"] for sample in samples)
    total_errors = sum(sample["errors"] for sample in samples)
    return {
        "target": {"base_url": base_url, "path": path},
        "settings": {
            "concurrency_levels": list(concurrency_levels),
            "requests_per_level": requests_per_level,
            "timeout_seconds": timeout_seconds,
            "latency_ceiling_ms": latency_ceiling_ms,
        },
        "summary": {
            "total_requests": total_requests,
            "total_errors": total_errors,
            "max_sustained_concurrency": max_sustained,
        },
        "samples": samples,
    }


def write_concurrency_report(report: dict[str, Any], output: Path) -> None:
    """Write a benchmark report as stable, pretty JSON."""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def format_concurrency_summary(report: dict[str, Any]) -> str:
    """Return a short Markdown summary for humans reading benchmark output."""
    target = report["target"]
    summary = report["summary"]
    lines = [
        "# Concurrency Benchmark",
        "",
        f"- Target path: `{target['path']}`",
        f"- Total requests: {summary['total_requests']}",
        f"- Total errors: {summary['total_errors']}",
        f"- Max sustained concurrency: {summary['max_sustained_concurrency']}",
        "",
        "| Clients | Requests | Errors | Throughput req/s | p50 ms | p95 ms | p99 ms |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for sample in report["samples"]:
        latency = sample["latency_ms"]
        lines.append(
            "| {concurrency} | {requests} | {errors} | {throughput:.3f} | "
            "{p50} | {p95} | {p99} |".format(
                concurrency=sample["concurrency"],
                requests=sample["requests"],
                errors=sample["errors"],
                throughput=sample["throughput_rps"],
                p50=latency["p50"],
                p95=latency["p95"],
                p99=latency["p99"],
            )
        )
    return "\n".join(lines) + "\n"


def _load_mock_server(repo_root: Path) -> Any:
    server_module = repo_root / "k8s" / "sandbox" / "mock_bmc_server.py"
    spec = importlib.util.spec_from_file_location("mock_bmc_server", server_module)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise RuntimeError(f"could not load {server_module}")
    spec.loader.exec_module(module)
    return module


def _parse_levels(value: str) -> tuple[int, ...]:
    try:
        levels = tuple(int(part.strip()) for part in value.split(",") if part.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("levels must be comma-separated integers") from exc
    if not levels or any(level < 1 for level in levels):
        raise argparse.ArgumentTypeError("levels must contain positive integers")
    return levels


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run an opt-in mock-BMC concurrency benchmark."
    )
    repo_root = Path(__file__).resolve().parents[1]
    parser.add_argument("--levels", type=_parse_levels, default=(1, 8, 32, 128))
    parser.add_argument("--requests-per-level", type=int, default=128)
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    parser.add_argument("--latency-ceiling-ms", type=float, default=5000.0)
    parser.add_argument("--path", default="/redfish/v1/Systems/System_0")
    parser.add_argument(
        "--corpus-tarball",
        type=Path,
        default=repo_root / "tests" / "supermicro_gb300_corpus.tar.gz",
    )
    parser.add_argument("--corpus-leaf", default="172.25.230.37")
    parser.add_argument(
        "--concurrency-report",
        type=Path,
        default=repo_root / "reports" / "concurrency-benchmark.json",
    )
    parser.add_argument(
        "--summary-report",
        type=Path,
        default=repo_root / "reports" / "concurrency-benchmark.md",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]

    from vendor_corpus import corpus_dir

    corpus = corpus_dir(args.corpus_tarball, args.corpus_leaf)
    server_module = _load_mock_server(repo_root)
    with server_module.run_server("127.0.0.1", 0, corpus) as server:
        base_url = "http://{}:{}".format(*server.server_address)
        report = run_concurrency_benchmark(
            base_url,
            args.path,
            concurrency_levels=args.levels,
            requests_per_level=args.requests_per_level,
            timeout_seconds=args.timeout_seconds,
            latency_ceiling_ms=args.latency_ceiling_ms,
        )
    report["target"]["base_url"] = "local mock BMC"

    write_concurrency_report(report, args.concurrency_report)
    summary = format_concurrency_summary(report)
    args.summary_report.parent.mkdir(parents=True, exist_ok=True)
    args.summary_report.write_text(summary, encoding="utf-8")
    print(summary)

    expected_max = max(args.levels)
    if report["summary"]["total_errors"]:
        return 1
    if report["summary"]["max_sustained_concurrency"] < expected_max:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
