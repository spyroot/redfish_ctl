"""SignalFx (Splunk Observability) writer for telemetry samples.

The SignalFx backend wraps shared samples into ``/v2/datapoint`` envelopes,
pushes them to Splunk ingest, and (with ``--verify-readback``) confirms the
datapoints are visible in Splunk MTS — because a POST returning 200 is not proof
of ingestion (issue #363). The emission itself lives in :mod:`.emit`; this file
is the :class:`AbstractExporterWriter` adapter that owns the ``--signalfx-*`` /
``SPLUNK_*`` config.

Author Mus spyroot@gmail.com
"""

from __future__ import annotations

import time
from typing import Callable, Iterable, Optional

from redfish_ctl.config import signalfx_api_token, signalfx_realm
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.telemetry.abstract_exporter_writer import AbstractExporterWriter
from redfish_ctl.telemetry.exporter import common_sample_dimensions
from redfish_ctl.telemetry.metric_model import MetricSample

from . import emit


class SignalFxWriter(AbstractExporterWriter):
    """SignalFx writer.

    Implements :class:`AbstractExporterWriter`. ``write_once`` renders the
    SignalFx datapoint body and, when pushing, POSTs it and optionally confirms
    ingestion via a Splunk MTS readback; ``run`` pushes on a fixed interval
    forever. It owns its SignalFx config (``--signalfx-*`` / ``SPLUNK_*``).
    """

    def __init__(self, *,
                 ingest_url: Optional[str] = None,
                 token: Optional[str] = None,
                 token_env: Optional[str] = None,
                 token_file: Optional[str] = None,
                 realm: Optional[str] = None,
                 api_token_env: Optional[str] = None,
                 verify_readback: bool = False,
                 freshness_ms: int = 900000,
                 interval: float = 30.0,
                 push: bool = True):
        """Initialize the writer with its SignalFx config.

        :param ingest_url: SignalFx datapoint ingest URL; falls back to SPLUNK_INGEST_URL.
        :param token: direct SignalFx ingest token value.
        :param token_env: env var name holding the SignalFx ingest token.
        :param token_file: path to a file holding the SignalFx ingest token.
        :param realm: Splunk Observability realm for readback; falls back to SPLUNK_O11Y_REALM.
        :param api_token_env: env var holding the Splunk API (read) token; defaults to SPLUNK_API_TOKEN.
        :param verify_readback: when True, confirm ingestion via a Splunk MTS readback.
        :param freshness_ms: how recent a readback series must be to count as this push.
        :param interval: seconds between pushes in ``run`` mode.
        :param push: when True, POST datapoints; when False, ``write_once`` only
            renders the datapoint body (no network).
        """
        self._verify_readback = verify_readback
        self._freshness_ms = freshness_ms
        self._interval = interval
        self._push = push
        # Resolve the SignalFx config ONCE here, at the construction boundary, so
        # write_once/run receive concrete values and the SPLUNK_* env is read in a
        # single place per run rather than on every push path. Only resolve what
        # the mode needs: no token/URL when not pushing, no readback creds unless
        # verifying (resolving an absent ingest URL raises, which is fail-fast).
        if push:
            self._resolved_token = emit.resolve_signalfx_token(
                token_env, token=token, token_file=token_file)
            self._resolved_ingest_url = emit.resolve_signalfx_ingest_url(ingest_url)
        else:
            self._resolved_token = None
            self._resolved_ingest_url = None
        if push and verify_readback:
            self._resolved_realm = signalfx_realm(realm)
            self._resolved_api_token = signalfx_api_token(api_token_env)
        else:
            self._resolved_realm = ""
            self._resolved_api_token = ""

    def write_once(self, samples: Iterable[MetricSample]) -> CommandResult:
        """Render (and, when pushing, POST) one scrape of samples to SignalFx.

        :param samples: shared samples to emit.
        :return: CommandResult with the datapoint body or, on ``--verify-readback``,
            the compact canary summary and verdict.
        """
        materialized = list(samples)
        body = emit.to_signalfx_body(materialized)
        if not self._push:
            return CommandResult(
                body, None, {"sample_count": len(materialized)}, None)

        token = self._resolved_token
        ingest_url = self._resolved_ingest_url
        push_start = time.monotonic()
        status = emit.push_signalfx(body, token, ingest_url)
        push_ms = int((time.monotonic() - push_start) * 1000)
        if not self._verify_readback:
            return CommandResult(
                body, None,
                {"sample_count": len(materialized), "push_status": status,
                 "ingest_url": ingest_url},
                None)

        # A POST returning 200 is not proof: confirm the series are visible in MTS.
        realm = self._resolved_realm
        api_token = self._resolved_api_token
        if not realm or not api_token:
            raise ValueError(
                "--verify-readback needs a realm (--signalfx-realm or "
                "SPLUNK_O11Y_REALM) and an API token (--signalfx-api-token-env "
                "or SPLUNK_API_TOKEN)")
        metric_names = sorted({sample.metric for sample in materialized})
        dimensions = common_sample_dimensions(materialized)
        readback_start = time.monotonic()
        readback = emit.verify_signalfx_readback(realm, api_token, metric_names, dimensions)
        readback_ms = int((time.monotonic() - readback_start) * 1000)
        summary, error = emit.build_readback_result(
            status, ingest_url, len(materialized), metric_names, readback,
            {"scrape": 0, "push": push_ms, "readback": readback_ms},
            freshness_ms=self._freshness_ms)
        return CommandResult(summary, None, summary, error)

    def run(self, scrape_samples: Callable[[], list[MetricSample]]) -> None:
        """Push SignalFx datapoints on a fixed interval until interrupted.

        :param scrape_samples: callable returning fresh samples for each scrape.
        """
        emit.run_signalfx_loop(
            scrape_samples, self._resolved_token, self._resolved_ingest_url,
            self._interval)
