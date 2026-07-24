"""Verify streamed metrics are visible in Splunk Observability (the live gate).

    python tools/splunk_metric_gate.py
    python tools/splunk_metric_gate.py hw.health hw.fabric.link_down_reason
    python tools/splunk_metric_gate.py --since-minutes 30 --metrics-file specs/telemetry/gate-metrics.txt
    python tools/splunk_metric_gate.py --expected-build-revision <commit> \
        --expected-schema-contract-version <version> \
        --expected-hosts-file <hosts.txt>

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
import re
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
    "hw.bmc.up",
    "hw.build_info",
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
    parser.add_argument(
        "--expected-build-revision",
        default=None,
        help="exact revision every expected host must report via hw.build_info",
    )
    parser.add_argument(
        "--expected-hosts-file",
        default=None,
        help="file with every expected host.name value, one per line",
    )
    parser.add_argument(
        "--expected-schema-contract-version",
        default=None,
        help="exact telemetry catalog version every expected host must report",
    )
    parser.add_argument(
        "--host-dimension",
        default="host.name",
        help="identity dimension used for expected hosts (default: host.name)",
    )
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


def load_expected_hosts(path: str) -> list[str]:
    """Load an ordered, de-duplicated expected-host inventory.

    :param path: file with one host identity per line; ``#`` starts a comment.
    :return: non-empty expected host identities.
    :raises ValueError: when the file cannot be read or contains no hosts.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            hosts = [line.split("#", 1)[0].strip() for line in handle]
    except OSError as exc:
        raise ValueError(f"cannot read expected hosts file: {path}") from exc
    ordered = list(dict.fromkeys(host for host in hosts if host))
    if not ordered:
        raise ValueError("expected hosts file is empty")
    return ordered


def _escape_query_value(value: str) -> str:
    """Escape one metric-search string value.

    :param value: raw metric or dimension value.
    :return: value safe inside a quoted metric-search term.
    """
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _metric_query(metric: str, dimensions: dict[str, str] | None = None) -> str:
    """Build a metric-time-series query scoped by exact dimensions.

    :param metric: metric name to query.
    :param dimensions: optional dimension key/value filters.
    :return: Splunk metric-search expression.
    """
    terms = [f'sf_metric:"{_escape_query_value(metric)}"']
    for key, value in sorted((dimensions or {}).items()):
        terms.append(f'{key}:"{_escape_query_value(value)}"')
    return " AND ".join(terms)


def query_metric(
        realm: str,
        token: str,
        metric: str,
        timeout: float,
        dimensions: dict[str, str] | None = None) -> dict:
    """Query Splunk Observability for time series of one metric.

    :param realm: Splunk Observability realm (for example ``us1``).
    :param token: API token value (sent as the X-SF-Token header, never logged).
    :param metric: metric name to look up.
    :param timeout: HTTP timeout in seconds.
    :param dimensions: optional exact dimensions that scope the metric search.
    :return: dict with ``count`` (matching time series), ``newest_ms``
        (latest lastUpdated/created millisecond timestamp seen, 0 when none),
        and the raw ``results`` rows used by the fleet build-identity gate.
    :raises urllib.error.URLError: on transport failure.
    :raises ValueError: when the API answers with a non-JSON body.
    """
    query = urllib.parse.urlencode(
        {
            "query": _metric_query(metric, dimensions),
            "limit": 50,
            "orderBy": "-sf_updatedOnMs",
        })
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
    return {
        "count": int(data.get("count") or len(results)),
        "newest_ms": newest,
        "results": results,
    }


def _is_fresh(info: dict, cutoff_ms: float) -> bool:
    """Return whether a query found at least one recently updated series.

    :param info: ``query_metric`` count and newest timestamp result.
    :param cutoff_ms: oldest accepted update timestamp, in milliseconds.
    :return: True only when a matching series is present and fresh.
    """
    return info["count"] > 0 and info["newest_ms"] >= cutoff_ms


def _row_timestamp_ms(row: dict) -> int:
    """Return the newest known update timestamp for one MTS result row.

    :param row: metric-time-series result row.
    :return: newest timestamp in milliseconds, or zero when unavailable.
    """
    stamps = [
        row.get(key)
        for key in ("lastUpdated", "sf_updatedOnMs", "updatedOnMs", "created")
    ]
    return int(max(
        (stamp for stamp in stamps if isinstance(stamp, (int, float))),
        default=0,
    ))


def _row_dimension(row: dict, name: str) -> str | None:
    """Read a dimension from common Splunk MTS response shapes.

    :param row: metric-time-series result row.
    :param name: dimension name to read.
    :return: string value when present, else None.
    """
    for container_name in ("dimensions", "customProperties"):
        container = row.get(container_name)
        if isinstance(container, dict) and container.get(name) not in (None, ""):
            return str(container[name])
        if isinstance(container, list):
            for item in container:
                if not isinstance(item, dict):
                    continue
                key = item.get("key") or item.get("name")
                value = item.get("value")
                if key == name and value not in (None, ""):
                    return str(value)
    if row.get(name) not in (None, ""):
        return str(row[name])
    return None


def _fresh_build_identities(info: dict, cutoff_ms: float) -> set[tuple[str, str]]:
    """Return fresh ``(commit, schema version)`` identities in an MTS response.

    :param info: query result containing raw MTS rows when available.
    :param cutoff_ms: oldest accepted update timestamp, in milliseconds.
    :return: distinct complete build identities updated within the window.
    """
    identities: set[tuple[str, str]] = set()
    for row in info.get("results", []):
        if not isinstance(row, dict) or _row_timestamp_ms(row) < cutoff_ms:
            continue
        commit = _row_dimension(row, "commit")
        schema_version = _row_dimension(row, "schema_contract_version")
        if commit and schema_version:
            identities.add((commit, schema_version))
    return identities


def check_build_revision_fleet(
        realm: str,
        token: str,
        revision: str,
        schema_contract_version: str,
        hosts: list[str],
        host_dimension: str,
        cutoff_ms: float,
        timeout: float) -> int:
    """Check every expected host for fresh build-info at one exact revision.

    :param realm: Splunk Observability realm.
    :param token: API token value, never printed.
    :param revision: exact desired deployment revision.
    :param schema_contract_version: exact desired telemetry catalog version.
    :param hosts: complete expected host identity list.
    :param host_dimension: metric dimension used to identify a host.
    :param cutoff_ms: oldest accepted update timestamp, in milliseconds.
    :param timeout: per-request HTTP timeout.
    :return: number of hosts that are missing or report another revision.
    """
    matching = 0
    mismatched = 0
    missing = 0
    mixed_hosts = 0
    for host in hosts:
        host_scope = {host_dimension: host}
        try:
            any_info = query_metric(
                realm,
                token,
                "hw.build_info",
                timeout,
                host_scope,
            )
        except Exception as exc:
            print(
                f"FAIL build-info {host}: query error: "
                f"{type(exc).__name__}: {exc}"
            )
            missing += 1
            continue
        if not _is_fresh(any_info, cutoff_ms):
            print(f"FAIL build-info {host}: no fresh hw.build_info series")
            missing += 1
            continue
        expected_identity = (revision, schema_contract_version)
        observed_identities = _fresh_build_identities(any_info, cutoff_ms)
        if not observed_identities:
            print(
                f"FAIL build-info {host}: fresh series has no verifiable "
                "commit and schema identity"
            )
            mismatched += 1
            continue
        if expected_identity not in observed_identities:
            print(
                f"FAIL build-info {host}: fresh series does not match "
                f"revision {revision} and schema {schema_contract_version}"
            )
            mismatched += 1
            continue
        if observed_identities != {expected_identity}:
            print(
                f"FAIL build-info {host}: multiple fresh build identities "
                "detected"
            )
            mismatched += 1
            mixed_hosts += 1
            continue
        print(
            f"PASS build-info {host}: revision {revision}, "
            f"schema {schema_contract_version}"
        )
        matching += 1
    if mixed_hosts or (matching and mismatched):
        print("FAIL build-info fleet: mixed build identities detected")
    print(
        f"build-info-gate: {matching}/{len(hosts)} hosts match; "
        f"mismatched={mismatched} missing={missing}"
    )
    return mismatched + missing


def run_gate(argv: list[str] | None = None) -> int:
    """Run the gate and print one PASS/FAIL line per metric.

    :param argv: CLI arguments (None uses sys.argv).
    :return: process exit code — 0 all seen, 1 any miss/stale, 2 config error.
    """
    args = build_parser().parse_args(argv)
    revision_requested = args.expected_build_revision is not None
    hosts_requested = args.expected_hosts_file is not None
    schema_requested = args.expected_schema_contract_version is not None
    if len({revision_requested, hosts_requested, schema_requested}) != 1:
        print(
            "splunk-gate: --expected-build-revision, "
            "--expected-schema-contract-version, and --expected-hosts-file "
            "must be used together",
            file=sys.stderr,
        )
        return 2
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}", args.host_dimension):
        print("splunk-gate: --host-dimension is invalid", file=sys.stderr)
        return 2
    expected_hosts: list[str] = []
    if hosts_requested:
        try:
            expected_hosts = load_expected_hosts(args.expected_hosts_file)
        except ValueError as exc:
            print(f"splunk-gate: {exc}", file=sys.stderr)
            return 2
        args.expected_build_revision = args.expected_build_revision.strip()
        args.expected_schema_contract_version = (
            args.expected_schema_contract_version.strip()
        )
        if (
            not args.expected_build_revision
            or not args.expected_schema_contract_version
        ):
            print("splunk-gate: expected build identity is empty", file=sys.stderr)
            return 2
    realm = args.realm or os.environ.get("SPLUNK_O11Y_REALM", "")
    # Query needs an API-scoped token; ingest-scoped tokens get 401s from
    # the metrics API, so prefer SPLUNK_API_TOKEN when the caller kept the
    # default env name and it is set.
    token = os.environ.get(args.token_env, "")
    if not token or args.token_env == "SPLUNK_ACCESS_TOKEN":
        token = os.environ.get("SPLUNK_API_TOKEN", "") or token
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
    if expected_hosts:
        failures += check_build_revision_fleet(
            realm=realm,
            token=token,
            revision=args.expected_build_revision,
            schema_contract_version=args.expected_schema_contract_version,
            hosts=expected_hosts,
            host_dimension=args.host_dimension,
            cutoff_ms=cutoff_ms,
            timeout=args.timeout,
        )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run_gate())
