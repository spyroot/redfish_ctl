"""SignalFx (Splunk Observability) emission for the telemetry exporter.

Emits the shared ``hw.*`` metric contract to Splunk Observability: it wraps
samples into the SignalFx ``/v2/datapoint`` envelopes, pushes them, and (issue
#363) reads the metric time series back from Splunk MTS to confirm ingestion,
since a POST returning 200 is not proof the datapoints landed. Token/URL
resolution and readback verdicts live here too. Vendor-neutral: it consumes the
shared MetricSample model, so one SignalFx writer serves every vendor reader.

Author Mus spyroot@gmail.com
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional

from redfish_ctl.telemetry import http_util
from redfish_ctl.telemetry import identity as identity_mod
from redfish_ctl.telemetry.exporter import _non_empty, jittered_interval, metric_definition
from redfish_ctl.telemetry.metric_model import MetricSample


SIGNALFX_DATAPOINT_PATH = "/v2/datapoint"


def to_signalfx_body(samples: Iterable[MetricSample]) -> dict[str, list[dict]]:
    """Wrap samples in the SignalFx /v2/datapoint typed envelopes.

    :param samples: metric samples to wrap.
    :return: SignalFx ``/v2/datapoint`` body with typed datapoint lists.
    """
    body = {"gauge": [], "counter": [], "cumulative_counter": []}
    for sample in samples:
        definition = metric_definition(sample.metric)
        envelope = (
            "cumulative_counter" if definition.kind == "counter"
            else definition.kind
        )
        body[envelope].append({
            "metric": sample.metric,
            "value": sample.value,
            "dimensions": {
                key: value
                for key, value in sample.dimensions.items()
                if key not in identity_mod.RESOURCE_ONLY_DIMENSIONS
            },
        })
    return {kind: points for kind, points in body.items() if points}


def _require_datapoint_url(ingest_url: str) -> str:
    """Return ``ingest_url`` when it is a full SignalFx datapoint endpoint, else raise.

    ``push_signalfx`` POSTs the URL as-is (it does not append a path), so a bare
    host such as ``https://ingest.us1.observability.splunkcloud.com`` accepts the
    request context but silently drops every datapoint. Require the full
    ``…/v2/datapoint`` endpoint so misconfiguration fails loudly instead. The host
    itself is not restricted: ``ingest.<realm>.observability.splunkcloud.com`` is
    the current Splunk ingest host and ``ingest.<realm>.signalfx.com`` is the
    legacy one — both are accepted.

    :param ingest_url: the SignalFx ingest URL to validate.
    :return: ``ingest_url`` unchanged when it is a full datapoint endpoint.
    :raises ValueError: if the URL is not a full ``…/v2/datapoint`` endpoint.
    """
    parsed = http_util.require_https_url(ingest_url, "SignalFx ingest URL")
    if parsed.path.rstrip("/") != SIGNALFX_DATAPOINT_PATH:
        raise ValueError(
            "SignalFx ingest URL must be the full datapoint endpoint ending in "
            f"{SIGNALFX_DATAPOINT_PATH} (e.g. "
            "https://ingest.us1.observability.splunkcloud.com/v2/datapoint), not a "
            f"bare host; got {ingest_url!r}")
    return ingest_url


def resolve_signalfx_token(
        token_env: Optional[str] = None,
        token: Optional[str] = None,
        token_file: Optional[str] = None) -> str:
    """Return the SignalFx ingest token from direct, file, or env source.

    :param token_env: env var name to read the token from; defaults to ``SPLUNK_ACCESS_TOKEN``.
    :param token: direct token value.
    :param token_file: path to a file containing the token.
    :return: the ingest token value.
    :raises ValueError: if the chosen source is unset or empty.
    """
    direct_token = _non_empty(token)
    if direct_token is not None:
        return str(direct_token)
    file_path = _non_empty(token_file)
    if file_path is not None:
        value = Path(str(file_path)).expanduser().read_text(encoding="utf-8").strip()
        if not value:
            raise ValueError(f"{file_path} is empty")
        return value
    name = token_env or "SPLUNK_ACCESS_TOKEN"
    token = os.environ.get(name, "")
    if not token:
        raise ValueError(f"{name} is not set")
    return token


def resolve_signalfx_ingest_url(ingest_url: Optional[str] = None) -> str:
    """Return a validated SignalFx datapoint ingest URL.

    Falls back to the ``SPLUNK_INGEST_URL`` environment variable and requires the
    full ``…/v2/datapoint`` endpoint (see ``_require_datapoint_url``).

    :param ingest_url: explicit ingest URL; falls back to ``SPLUNK_INGEST_URL``.
    :return: a validated full ``…/v2/datapoint`` ingest URL.
    :raises ValueError: if no URL is set or it is not a full datapoint endpoint.
    """
    url = ingest_url or os.environ.get("SPLUNK_INGEST_URL", "")
    if not url:
        raise ValueError("SPLUNK_INGEST_URL is not set")
    return _require_datapoint_url(url)


def push_signalfx(body: Mapping, token: str, ingest_url: str, timeout: float = 20.0) -> int:
    """POST a SignalFx datapoint body and return the status code.

    ``ingest_url`` must be the full SignalFx datapoint endpoint (``…/v2/datapoint``);
    it is POSTed verbatim, so a bare host silently drops every datapoint
    (see ``_require_datapoint_url``).

    :param body: SignalFx datapoint payload to POST.
    :param token: SignalFx ingest token for the ``X-SF-Token`` header.
    :param ingest_url: full SignalFx datapoint endpoint (``…/v2/datapoint``).
    :param timeout: request timeout in seconds.
    :return: the HTTP status code of the POST response.
    :raises ValueError: if ``ingest_url`` is not a full datapoint endpoint.
    """
    ingest_url = _require_datapoint_url(ingest_url)
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        ingest_url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", "X-SF-Token": token},
    )
    with http_util.open_no_redirect_request(req, timeout=timeout) as response:
        return response.status


def _mts_query(metric: str, dimensions: Optional[Mapping] = None) -> str:
    """Build a metrictimeseries query for one metric, scoped by dimensions.

    A metric time series is identified by the metric name AND its dimension set
    (Splunk data model), so scoping by a unique dimension such as ``host.name``
    reads back this host's series rather than every host that reports the metric.

    :param metric: the SignalFx metric name.
    :param dimensions: dimension key->value pairs to AND into the query.
    :return: the SignalFx search query string.
    """
    terms = [f'sf_metric:"{_escape_mts_value(metric)}"']
    for key, value in sorted((dimensions or {}).items()):
        terms.append(f'{key}:"{_escape_mts_value(value)}"')
    return " AND ".join(terms)


def _escape_mts_value(value) -> str:
    """Escape a value for a Splunk Observability MTS search term.

    :param value: raw metric or dimension value.
    :return: escaped string representation.
    """
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def signalfx_metric_readback(
        realm: str, api_token: str, metric: str,
        dimensions: Optional[Mapping] = None, timeout: float = 20.0) -> dict:
    """Return how many time series exist for a metric in Splunk MTS, and how fresh.

    A SignalFx datapoint POST returns HTTP 200/``OK`` even when the datapoints are
    not recorded, so ingest success must be confirmed by reading the metric time
    series back — not by trusting the POST status (issue #363). Because Splunk
    retains inactive series for 13 months, the caller must also check freshness
    (``newest_ms``), not merely a nonzero count.

    :param realm: Splunk Observability realm (for example ``us1``).
    :param api_token: API (read) token, sent as the ``X-SF-Token`` header; never logged.
    :param metric: SignalFx metric name to look up.
    :param dimensions: dimension key->value pairs to scope the query to one entity.
    :param timeout: HTTP timeout in seconds.
    :return: ``{"count": <matching series>, "newest_ms": <latest update ms, 0 if none>,
        "server_ms": <response Date header in ms, 0 if unavailable>}``.
    :raises ValueError: when the API answers with a non-JSON body.
    """
    query = urllib.parse.urlencode(
        {"query": _mts_query(metric, dimensions), "limit": 50,
         "orderBy": "-sf_updatedOnMs"})
    url = f"https://api.{realm}.signalfx.com/v2/metrictimeseries?{query}"
    http_util.require_https_url(url, "SignalFx readback URL")
    request = urllib.request.Request(url, headers={"X-SF-Token": api_token})
    with http_util.open_no_redirect_request(request, timeout=timeout) as response:
        server_ms = _response_date_ms(response)
        raw = response.read()
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"non-JSON metrictimeseries response for {metric}") from exc
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
        "server_ms": server_ms,
    }


def verify_signalfx_readback(
        realm: str, api_token: str, metrics: Iterable[str],
        dimensions: Optional[Mapping] = None, timeout: float = 20.0) -> dict:
    """Confirm each pushed metric is visible in Splunk MTS for one entity.

    :param realm: Splunk Observability realm.
    :param api_token: API (read) token; never logged.
    :param metrics: metric names to confirm (the names that were pushed).
    :param dimensions: dimension key->value pairs scoping the query to this host.
    :param timeout: per-query HTTP timeout in seconds.
    :return: ``{metric: {"count": int, "newest_ms": int, "server_ms": int}}`` for each metric.
    """
    return {metric: signalfx_metric_readback(realm, api_token, metric, dimensions, timeout)
            for metric in sorted(set(metrics))}


def build_readback_result(
        push_status: int, ingest_url: str, sample_count: int, metric_names: list,
        readback: dict, timing_ms: dict, now_ms: Optional[int] = None,
        freshness_ms: int = 900000) -> tuple:
    """Build the compact canary summary and verdict from a readback.

    A metric is confirmed only when its readback series is BOTH present
    (``count`` > 0) AND fresh (``newest_ms`` within ``freshness_ms`` of ``now_ms``).
    Splunk retains inactive series for 13 months, so a nonzero count alone can be a
    stale series this push did not create; a missing or stale metric is an error:
    the POST succeeded yet the datapoints were not ingested now (issue #363).

    :param push_status: the HTTP status the SignalFx POST returned.
    :param ingest_url: the ingest URL that was POSTed to.
    :param sample_count: number of samples scraped and pushed.
    :param metric_names: the distinct metric names that were pushed.
    :param readback: ``{metric: {"count": int, "newest_ms": int, "server_ms": int}}`` from MTS.
    :param timing_ms: ``{"scrape": int, "push": int, "readback": int}`` durations.
    :param now_ms: current wall-clock time in ms, for the freshness window. When
        omitted, freshness is anchored to the Splunk API response server time.
    :param freshness_ms: how recent ``newest_ms`` must be to count as this push.
    :return: ``(summary_dict, error_or_None)`` -- the compact result and verdict.
    """
    anchor_ms = now_ms
    clock_source = "caller"
    if anchor_ms is None:
        anchor_ms = _readback_server_ms(readback)
        clock_source = "signalfx_http_date" if anchor_ms is not None else "unavailable"

    if anchor_ms is None:
        fresh = []
    else:
        fresh = sorted(
            name for name, series in readback.items()
            if series["count"] > 0 and series["newest_ms"] >= anchor_ms - freshness_ms)
    missing = sorted(set(metric_names) - set(fresh))
    clock_error = None
    if anchor_ms is None:
        clock_error = (
            "SignalFx readback did not include server time, so freshness cannot be "
            "verified without trusting the local exporter clock")
    summary = {
        "push_status": push_status,
        "ingest_url": ingest_url,
        "sample_count": sample_count,
        "metrics_pushed": len(metric_names),
        "metrics_fresh": len(fresh),
        "missing_metrics": missing,
        "readback": readback,
        "timing_ms": timing_ms,
        "freshness_ms": freshness_ms,
        "readback_now_ms": anchor_ms,
        "clock_source": clock_source,
    }
    if clock_error:
        error = clock_error
    elif missing:
        error = (
            f"SignalFx POST returned {push_status} but {len(missing)} of "
            f"{len(metric_names)} pushed metrics have no fresh time series in Splunk "
            "MTS -- the POST succeeded yet the datapoints were not ingested")
    else:
        error = None
    return summary, error


def _response_date_ms(response) -> int:
    """Return the HTTP Date header as epoch milliseconds, or 0 if absent.

    :param response: urllib response object from the MTS readback request.
    :return: epoch milliseconds parsed from the Date header, or 0 when absent.
    """
    header = None
    getheader = getattr(response, "getheader", None)
    if callable(getheader):
        header = getheader("Date")
    if not header:
        headers = getattr(response, "headers", None)
        getter = getattr(headers, "get", None)
        if callable(getter):
            header = getter("Date")
    if not header:
        return 0
    try:
        parsed = parsedate_to_datetime(str(header))
    except (TypeError, ValueError):
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _readback_server_ms(readback: Mapping) -> Optional[int]:
    """Return the newest server timestamp reported by readback API responses.

    :param readback: per-metric readback summaries from Splunk MTS.
    :return: newest server timestamp in milliseconds, or None when unavailable.
    """
    stamps = []
    for series in readback.values():
        if not isinstance(series, Mapping):
            continue
        try:
            stamp = int(series.get("server_ms") or 0)
        except (TypeError, ValueError):
            continue
        if stamp > 0:
            stamps.append(stamp)
    return max(stamps) if stamps else None


def _report_signalfx_loop_error(exc: Exception) -> None:
    """Report a failed SignalFx push without stopping the exporter loop.

    :param exc: exception raised while scraping or pushing a SignalFx datapoint batch.
    """
    print(f"SignalFx push failed: {type(exc).__name__}: {exc}", file=sys.stderr)


def run_signalfx_loop(
        scrape_samples: Callable[[], list[MetricSample]],
        token: str,
        ingest_url: str,
        interval: float,
        timeout: float = 20.0,
        on_error: Optional[Callable[[Exception], None]] = None) -> None:
    """Push SignalFx datapoints forever at ``interval`` seconds.

    :param scrape_samples: callable returning the samples to push each cycle.
    :param token: SignalFx ingest token.
    :param ingest_url: full SignalFx datapoint endpoint.
    :param interval: base seconds between pushes (jittered per cycle).
    :param timeout: per-push request timeout in seconds.
    :param on_error: optional callback for transient scrape or push failures.
    """
    report_error = on_error or _report_signalfx_loop_error
    while True:
        start = time.monotonic()
        try:
            push_signalfx(
                to_signalfx_body(scrape_samples()),
                token,
                ingest_url,
                timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001 - exporter must survive transient push failures
            report_error(exc)
        elapsed = time.monotonic() - start
        time.sleep(max(1.0, jittered_interval(interval) - elapsed))
