"""Verify streamed metrics are visible in Splunk Observability (the live gate).

    python tools/splunk_metric_gate.py
    python tools/splunk_metric_gate.py hw.health hw.fabric.link_down_reason
    python tools/splunk_metric_gate.py --metrics-file specs/telemetry/gate-metrics.txt

For every expected metric name the gate queries the Splunk Observability
metric-time-series API (``https://api.<realm>.signalfx.com/v2/metrictimeseries``)
and passes only when at least one returned time series has ``active: true``.
Exit code 0 = every metric flowing, 1 = any inactive/missing/error result, 2 =
configuration error. Nothing here prints credential values.

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
import sys
import urllib.error
import urllib.parse
import urllib.request

from redfish_ctl.config import (
    signalfx_access_token,
    signalfx_api_token,
    signalfx_realm,
)

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
    "hw.bmc.up",
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
                        choices=("SPLUNK_ACCESS_TOKEN", "SPLUNK_API_TOKEN"),
                        help="environment variable holding the API token")
    parser.add_argument("--since-minutes", type=float, default=30.0,
                        help="deprecated compatibility option; MTS active is authoritative")
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


def resolve_credentials(realm: str | None, token_env: str) -> tuple[str, str]:
    """Resolve the Splunk realm and API-scoped token without logging either.

    :param realm: explicit realm, or None to read ``SPLUNK_O11Y_REALM``.
    :param token_env: environment variable named by the caller.
    :return: ``(realm, token)``; either value may be empty for caller validation.
    """
    resolved_realm = signalfx_realm(realm)
    if token_env == "SPLUNK_API_TOKEN":
        token = signalfx_api_token()
    elif token_env == "SPLUNK_ACCESS_TOKEN":
        token = signalfx_api_token() or signalfx_access_token()
    else:
        raise ValueError(f"unsupported Splunk token environment name: {token_env}")
    return resolved_realm, token


def classify_liveness(info: dict) -> str:
    """Classify a parsed MTS response as FLOWING, INACTIVE, MISSING, or ERROR.

    :param info: normalized result returned by :func:`query_metric`.
    :return: one of ``FLOWING``, ``INACTIVE``, ``MISSING``, or ``ERROR``.
    """
    count = info.get("count")
    if isinstance(count, bool) or not isinstance(count, int):
        return "ERROR"
    if count <= 0:
        return "MISSING"
    active = info.get("active")
    if (info.get("active_complete") is not True or isinstance(active, bool)
            or not isinstance(active, int)):
        return "ERROR"
    return "FLOWING" if active > 0 else "INACTIVE"


def query_metric(realm: str, token: str, metric: str, timeout: float) -> dict:
    """Query Splunk Observability for time series of one metric.

    :param realm: Splunk Observability realm (for example ``us1``).
    :param token: API token value (sent as the X-SF-Token header, never logged).
    :param metric: metric name to look up.
    :param timeout: HTTP timeout in seconds.
    :return: dict with total ``count``, ``active`` series count,
        ``active_complete`` payload validity, and compatibility ``newest_ms``.
    :raises urllib.error.URLError: on transport failure.
    :raises ValueError: when the API answers with a non-JSON body.
    """
    query = urllib.parse.urlencode({"query": f'sf_metric:"{metric}"', "limit": 500})
    url = f"https://api.{realm}.signalfx.com/v2/metrictimeseries?{query}"
    request = urllib.request.Request(url, headers={"X-SF-Token": token})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise ValueError(f"non-JSON response for {metric}") from exc
    results = data.get("results") or []
    if not isinstance(results, list) or any(not isinstance(row, dict) for row in results):
        raise ValueError(f"invalid results payload for {metric}")
    active_complete = bool(results) and all(
        "active" in row and isinstance(row["active"], bool) for row in results)
    active = sum(1 for row in results if row.get("active") is True)
    newest = 0
    for row in results:
        for key in ("lastUpdated", "sf_updatedOnMs", "updatedOnMs", "created"):
            stamp = row.get(key)
            if isinstance(stamp, (int, float)) and stamp > newest:
                newest = int(stamp)
    raw_count = data.get("count")
    count = len(results) if raw_count is None else int(raw_count)
    return {
        "count": count,
        "active": active,
        "active_complete": active_complete,
        "newest_ms": newest,
    }


def run_gate(argv: list[str] | None = None) -> int:
    """Run the gate and print one PASS/FAIL line per metric.

    :param argv: CLI arguments (None uses sys.argv).
    :return: process exit code — 0 all flowing, 1 any liveness failure,
        2 configuration error.
    """
    args = build_parser().parse_args(argv)
    realm, token = resolve_credentials(args.realm, args.token_env)
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

    counts = {"FLOWING": 0, "INACTIVE": 0, "MISSING": 0, "ERROR": 0}
    for metric in metrics:
        try:
            info = query_metric(realm, token, metric, args.timeout)
        except Exception as exc:  # transport/auth/parse — fail loud per metric
            print(f"FAIL ERROR {metric}: query error: {type(exc).__name__}: {exc}")
            counts["ERROR"] += 1
            continue
        status = classify_liveness(info)
        counts[status] += 1
        if status == "FLOWING":
            print(f"PASS FLOWING {metric}: {info['active']}/{info['count']} active time series")
        elif status == "MISSING":
            print(f"FAIL MISSING {metric}: no time series found")
        elif status == "INACTIVE":
            print(f"FAIL INACTIVE {metric}: {info['count']} time series, none active")
        else:
            print(f"FAIL ERROR {metric}: active flag missing or malformed")
    print("splunk-gate: " + " ".join(f"{name}={counts[name]}" for name in counts)
          + f" TOTAL={len(metrics)}")
    return 0 if counts["FLOWING"] == len(metrics) else 1


if __name__ == "__main__":
    sys.exit(run_gate())
