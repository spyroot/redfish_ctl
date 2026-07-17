"""Verify streamed metrics are visible in Splunk Observability (the live gate).

    python tools/splunk_metric_gate.py
    python tools/splunk_metric_gate.py hw.health hw.fabric.link_down_reason
    python tools/splunk_metric_gate.py --since-minutes 30 --metrics-file specs/telemetry/gate-metrics.txt

For every expected metric name the gate queries the Splunk Observability
metric-time-series API (``https://api.<realm>.signalfx.com/v2/metrictimeseries``)
and passes only when at least one time series exists AND was updated inside
the freshness window. Exit code 0 = every metric seen, 1 = any miss, 2 =
configuration error. Designed to run inside the fleet dev container, where
the entrypoint already exports the token and realm; nothing here prints
credential values.

Configuration precedence (CLI > env), per the operator contract:

* token: ``--token-env`` names the variable (default ``SPLUNK_ACCESS_TOKEN``)
* realm: ``--realm`` flag, else ``SPLUNK_O11Y_REALM`` env
* metric list: positional names, else ``--metrics-file`` (one name per line,
  ``#`` comments allowed), else the built-in P0 set

Author Mus spyroot@gmail.com
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# The P0 telemetry set: health/state enums plus a core signal from each
# long-standing family, so a green gate means the whole pipeline is live.
DEFAULT_METRICS = [
    "hw.component.health",
    "hw.component.health_rollup",
    "hw.component.state",
    "hw.fabric.link_down_reason",
    "hw.power.edp_violation_state",
    "hw.power.break_performance_state",
    "hw.temperature",
    "hw.power",
    "hw.scrape.ok",
]


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser for the metric gate.

    :return: configured ArgumentParser.
    """
    parser = argparse.ArgumentParser(
        description="Check that expected metrics are visible in Splunk Observability.")
    parser.add_argument("metrics", nargs="*",
                        help="metric names to check; defaults to the built-in P0 set")
    parser.add_argument("--metrics-file", default=None,
                        help="file with one metric name per line (# comments allowed)")
    parser.add_argument("--realm", default=None,
                        help="Splunk Observability realm; defaults to SPLUNK_O11Y_REALM")
    parser.add_argument("--token-env", default="SPLUNK_ACCESS_TOKEN",
                        help="environment variable holding the API token")
    parser.add_argument("--since-minutes", type=float, default=30.0,
                        help="freshness window: newest time series update must be this recent")
    parser.add_argument("--timeout", type=float, default=15.0,
                        help="per-request HTTP timeout in seconds")
    return parser


def load_metrics(args: argparse.Namespace) -> list[str]:
    """Resolve the metric list from args, file, or the built-in default.

    :param args: parsed CLI arguments.
    :return: ordered, de-duplicated metric names.
    :raises ValueError: when the metrics file cannot be read.
    """
    names: list[str] = list(args.metrics or [])
    if not names and args.metrics_file:
        try:
            with open(args.metrics_file, encoding="utf-8") as handle:
                for line in handle:
                    text = line.split("#", 1)[0].strip()
                    if text:
                        names.append(text)
        except OSError as exc:
            raise ValueError(f"cannot read metrics file: {args.metrics_file}") from exc
    if not names:
        names = list(DEFAULT_METRICS)
    seen: set[str] = set()
    ordered = []
    for name in names:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def query_metric(realm: str, token: str, metric: str, timeout: float) -> dict:
    """Query Splunk Observability for time series of one metric.

    :param realm: Splunk Observability realm (for example ``us1``).
    :param token: API token value (sent as the X-SF-Token header, never logged).
    :param metric: metric name to look up.
    :param timeout: HTTP timeout in seconds.
    :return: dict with ``count`` (matching time series) and ``newest_ms``
        (latest lastUpdated/created millisecond timestamp seen, 0 when none).
    :raises urllib.error.URLError: on transport failure.
    :raises ValueError: when the API answers with a non-JSON body.
    """
    query = urllib.parse.urlencode(
        {"query": f'sf_metric:"{metric}"', "limit": 50, "orderBy": "-sf_updatedOnMs"})
    url = f"https://api.{realm}.signalfx.com/v2/metrictimeseries?{query}"
    request = urllib.request.Request(url, headers={"X-SF-Token": token})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise ValueError(f"non-JSON response for {metric}") from exc
    results = data.get("results") or []
    newest = 0
    for row in results:
        for key in ("lastUpdated", "sf_updatedOnMs", "updatedOnMs", "created"):
            stamp = row.get(key)
            if isinstance(stamp, (int, float)) and stamp > newest:
                newest = int(stamp)
    return {"count": int(data.get("count") or len(results)), "newest_ms": newest}


def run_gate(argv: list[str] | None = None) -> int:
    """Run the gate and print one PASS/FAIL line per metric.

    :param argv: CLI arguments (None uses sys.argv).
    :return: process exit code — 0 all seen, 1 any miss/stale, 2 config error.
    """
    args = build_parser().parse_args(argv)
    realm = args.realm or os.environ.get("SPLUNK_O11Y_REALM", "")
    token = os.environ.get(args.token_env, "")
    if not realm:
        print("splunk-gate: realm is not set (--realm or SPLUNK_O11Y_REALM)", file=sys.stderr)
        return 2
    if not token:
        print(f"splunk-gate: token env {args.token_env} is empty", file=sys.stderr)
        return 2
    try:
        metrics = load_metrics(args)
    except ValueError as exc:
        print(f"splunk-gate: {exc}", file=sys.stderr)
        return 2

    cutoff_ms = (time.time() - args.since_minutes * 60.0) * 1000.0
    failures = 0
    for metric in metrics:
        try:
            info = query_metric(realm, token, metric, args.timeout)
        except Exception as exc:  # transport/auth/parse — fail loud per metric
            print(f"FAIL {metric}: query error: {type(exc).__name__}: {exc}")
            failures += 1
            continue
        if info["count"] <= 0:
            print(f"FAIL {metric}: no time series found")
            failures += 1
        elif info["newest_ms"] <= 0:
            # A hard gate must never pass on existence alone: no timestamp in
            # the response means freshness cannot be verified, so fail loud.
            print(f"FAIL {metric}: {info['count']} time series but no update "
                  f"timestamp in the response — freshness unverifiable")
            failures += 1
        elif info["newest_ms"] < cutoff_ms:
            age_min = (time.time() * 1000.0 - info["newest_ms"]) / 60000.0
            print(f"FAIL {metric}: stale — newest update {age_min:.0f} min ago "
                  f"(window {args.since_minutes:.0f} min)")
            failures += 1
        else:
            print(f"PASS {metric}: {info['count']} time series")
    print(f"splunk-gate: {len(metrics) - failures}/{len(metrics)} metrics visible")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run_gate())
