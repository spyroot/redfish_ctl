"""Expose Redfish telemetry as Prometheus or SignalFx metrics.

    redfish_ctl exporter
    redfish_ctl exporter --once --output prometheus
    redfish_ctl exporter --once --output signalfx

The exporter is read-only. It walks modern Redfish telemetry resources and
normalizes them into the ``hw.*`` metric contract used by the GB300/NV72
observability demo.
"""
import os
import time
from abc import abstractmethod
from typing import Optional

from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import REDFISH_API, ApiRequestType, Singleton
from . import exporter
from .exporter import (
    build_identity_dimensions,
    build_metric_samples,
    render_prometheus_text,
    resolve_signalfx_ingest_url,
    resolve_signalfx_token,
    run_signalfx_loop,
    serve_prometheus,
    to_signalfx_body,
)


class Exporter(RedfishManagerBase,
               scm_type=ApiRequestType.Exporter,
               name='exporter',
               metaclass=Singleton):
    """Read BMC telemetry and expose Prometheus or SignalFx metric output."""

    def __init__(self, *args, **kwargs):
        """Initialize the exporter command."""
        super(Exporter, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``exporter`` subcommand.

        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser(is_file_save=False)
        cmd_parser.add_argument(
            "--listen", default="0.0.0.0", type=str,
            help="address for the Prometheus /metrics listener")
        cmd_parser.add_argument(
            "--port", default=9109, type=int,
            help="port for the Prometheus /metrics listener")
        cmd_parser.add_argument(
            "--interval", default=30.0, type=float,
            help="scrape interval in seconds for long-running output")
        cmd_parser.add_argument(
            "--once", action="store_true", default=False,
            help="scrape once and return the rendered output instead of serving forever")
        cmd_parser.add_argument(
            "--output", dest="exporter_output", default="prometheus",
            choices=("prometheus", "signalfx", "otlp"),
            help="output format for --once or push mode")
        cmd_parser.add_argument(
            "--label-bmc-ip", dest="label_bmc_ip", default=None, type=str,
            help="BMC IP used only for metric dimensions when different from IDRAC_IP")
        cmd_parser.add_argument(
            "--vendor", default=None, type=str,
            help="vendor dimension override, e.g. supermicro or dell")
        cmd_parser.add_argument(
            "--credential-file", dest="exporter_credential_file", default=None, type=str,
            help="gitignored KEY=VALUE runtime file for "
                 "REDFISH_IP/USERNAME/PASSWORD/PORT (legacy IDRAC_* also accepted)")
        cmd_parser.add_argument(
            "--exporter-config", dest="exporter_config_file", default=None, type=str,
            help="JSON exporter config spec for SignalFx ingest/token source and "
                 "identity dimension overrides")
        cmd_parser.add_argument(
            "--push-signalfx", action="store_true", default=False,
            help="push SignalFx datapoints instead of returning/serving Prometheus output")
        cmd_parser.add_argument(
            "--signalfx-ingest-url", dest="signalfx_ingest_url", default=None, type=str,
            help="SignalFx ingest URL; defaults to SPLUNK_INGEST_URL when pushing")
        cmd_parser.add_argument(
            "--signalfx-token-env", dest="signalfx_token_env", default=None,
            type=str, help="environment variable that holds the SignalFx ingest token")
        token_group = cmd_parser.add_mutually_exclusive_group(required=False)
        token_group.add_argument(
            "--signalfx-token", dest="signalfx_token", default=None, type=str,
            help="SignalFx ingest token value; prefer --signalfx-token-file or env "
                 "for unattended runs")
        token_group.add_argument(
            "--signalfx-token-file", dest="signalfx_token_file", default=None, type=str,
            help="file containing the SignalFx ingest token")
        cmd_parser.add_argument(
            "--verify-readback", dest="verify_readback", action="store_true",
            help="after a --once SignalFx push, read the metric time series back from "
                 "Splunk MTS and report a compact canary result; a POST returning 200 "
                 "is not treated as proof the datapoints were ingested (issue #363)")
        cmd_parser.add_argument(
            "--signalfx-realm", dest="signalfx_realm", default=None, type=str,
            help="Splunk Observability realm for readback; defaults to SPLUNK_O11Y_REALM")
        cmd_parser.add_argument(
            "--signalfx-api-token-env", dest="signalfx_api_token_env", default=None,
            type=str, help="env var holding the Splunk API (read) token for readback; "
                           "defaults to SPLUNK_API_TOKEN")
        cmd_parser.add_argument(
            "--identity-host-prefix", dest="identity_host_prefix",
            default=None, type=str,
            help="host.name prefix for derived exporter identity dimensions")
        cmd_parser.add_argument(
            "--identity-bmc-octet-base", dest="identity_bmc_octet_base",
            default=None, type=int,
            help="BMC last-octet base subtracted to derive the node slot")
        cmd_parser.add_argument(
            "--identity-server-octet-base", dest="identity_server_octet_base",
            default=None, type=int,
            help="server last-octet base added to the derived node slot")
        cmd_parser.add_argument(
            "--identity-server-subnet", dest="identity_server_subnet",
            default=None, type=str,
            help="server.address subnet override for derived identity dimensions")
        cmd_parser.add_argument(
            "--dimension", dest="identity_dimensions", action="append",
            default=None, metavar="KEY=VALUE",
            help="fixed dimension applied to every exported sample; repeat for "
                 "dashboard filters such as deployment.environment or model")
        cmd_parser.add_argument(
            "--otlp-endpoint", dest="otlp_endpoint", default=None, type=str,
            help="OTLP collector endpoint for --output otlp; defaults to "
                 "OTEL_EXPORTER_OTLP_ENDPOINT")
        cmd_parser.add_argument(
            "--otlp-protocol", dest="otlp_protocol", default=None,
            choices=("grpc", "http/protobuf"),
            help="OTLP transport for --output otlp; defaults to "
                 "OTEL_EXPORTER_OTLP_PROTOCOL, else grpc")
        help_text = ("serve Redfish telemetry as Prometheus /metrics, or push SignalFx "
                     "or OTLP (OpenTelemetry) datapoints")
        return cmd_parser, "exporter", help_text

    @staticmethod
    def _members(data):
        """Return @odata.id strings from a Redfish collection.

        :param data: parsed Redfish collection resource, or any value.
        :return: list of member @odata.id strings; empty when data is not a collection.
        """
        if not isinstance(data, dict):
            return []
        return [m["@odata.id"] for m in data.get("Members", [])
                if isinstance(m, dict) and isinstance(m.get("@odata.id"), str)]

    def _invoke_rows(self, api_type: ApiRequestType, name: str, **kwargs) -> list:
        """Invoke another read-only command and tolerate absent resources.

        :param api_type: ApiRequestType of the command to invoke.
        :param name: registered name of the command to invoke.
        :return: the command's list payload, or an empty list on error or non-list data.
        """
        try:
            result = self.sync_invoke(api_type, name, **kwargs)
        except Exception:
            return []
        return result.data if isinstance(result.data, list) else []

    def _invoke_dict(self, api_type: ApiRequestType, name: str, **kwargs) -> dict:
        """Invoke another read-only command that returns an object payload.

        :param api_type: ApiRequestType of the command to invoke.
        :param name: registered name of the command to invoke.
        :return: the command's dict payload, or an empty dict on error or non-dict data.
        """
        try:
            result = self.sync_invoke(api_type, name, **kwargs)
        except Exception:
            return {}
        return result.data if isinstance(result.data, dict) else {}

    def _leak_detection_rows(self, do_async: bool = False) -> list[dict]:
        """Return LeakDetector rows from the read-only leak-detectors command.

        :param do_async: when True, issue the underlying query asynchronously.
        :return: list of leak-detector rows; empty when none are exposed.
        """
        data = self._invoke_dict(
            ApiRequestType.LeakDetectors,
            "leak-detectors",
            do_async=do_async,
        )
        detectors = data.get("detectors") if isinstance(data, dict) else None
        return detectors if isinstance(detectors, list) else []

    def _environment_rows(self, do_async: bool = False) -> list[dict]:
        """Walk Chassis EnvironmentMetrics links and return their payloads.

        :param do_async: when True, issue the Redfish queries asynchronously.
        :return: list of EnvironmentMetrics payloads, one per chassis that exposes them.
        """
        rows = []
        try:
            chassis = self.base_query(REDFISH_API.Chassis, do_async=do_async).data or {}
        except Exception:
            return rows
        for chassis_uri in self._members(chassis):
            try:
                cdata = self.base_query(chassis_uri, do_async=do_async).data or {}
            except Exception:
                continue
            link = cdata.get("EnvironmentMetrics")
            env_uri = link.get("@odata.id") if isinstance(link, dict) else None
            if not env_uri:
                continue
            try:
                env_data = self.base_query(env_uri, do_async=do_async).data or {}
            except Exception:
                continue
            env_data["Chassis"] = cdata.get("Id") or chassis_uri.rsplit("/", 1)[-1]
            rows.append(env_data)
        return rows

    def _thermal_rows(self, do_async: bool = False, do_expanded: bool = False) -> list[dict]:
        """Return temperature readings from the read-only thermal command.

        :param do_async: when True, issue the underlying query asynchronously.
        :param do_expanded: when True, issue an expanded ($expand) query.
        :return: list of temperature-reading rows; empty when none are exposed.
        """
        try:
            result = self.sync_invoke(
                ApiRequestType.Thermal, "thermal",
                do_async=do_async, do_expanded=do_expanded)
        except Exception:
            return []
        data = result.data if isinstance(result.data, dict) else {}
        rows = data.get("temperature_readings")
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)]

    def _environment_command_rows(self, do_async: bool = False) -> list[dict]:
        """Return normalized rows from the environment-metrics command.

        Falls back to walking Chassis EnvironmentMetrics links directly when the
        command is unavailable or returns an unexpected payload.

        :param do_async: when True, issue the underlying queries asynchronously.
        :return: list of environment-metric rows; empty when none are exposed.
        """
        try:
            result = self.sync_invoke(
                ApiRequestType.EnvironmentMetrics,
                "environment-metrics",
                do_async=do_async,
            )
        except Exception:
            return self._environment_rows(do_async=do_async)
        data = result.data
        if isinstance(data, dict) and isinstance(data.get("metrics"), list):
            return data["metrics"]
        if isinstance(data, list):
            return data
        return self._environment_rows(do_async=do_async)

    def _vendor_label(self, vendor: Optional[str]) -> str:
        """Return a stable lower-case vendor label.

        :param vendor: explicit vendor override; when falsy the vendor is auto-detected.
        :return: the vendor label, or ``"unknown"`` when neither is available.
        """
        if vendor:
            return vendor
        try:
            detected = self.redfish_vendor
        except Exception:
            detected = ""
        return detected or "unknown"

    def collect_samples(self,
                        label_bmc_ip: Optional[str] = None,
                        vendor: Optional[str] = None,
                        do_async: bool = False,
                        do_expanded: bool = False,
                        identity_host_prefix: Optional[str] = None,
                        identity_bmc_octet_base: Optional[int] = None,
                        identity_server_octet_base: Optional[int] = None,
                        identity_server_subnet: Optional[str] = None,
                        identity_dimensions: Optional[list[str]] = None) -> list:
        """Scrape all supported read-only telemetry paths and build samples.

        :param label_bmc_ip: BMC IP used only for metric dimensions; defaults to the
            configured BMC address.
        :param vendor: vendor dimension override; auto-detected when None.
        :param do_async: when True, issue the Redfish queries asynchronously.
        :param do_expanded: when True, issue expanded ($expand) queries.
        :param identity_host_prefix: host.name prefix override.
        :param identity_bmc_octet_base: BMC last-octet base override for slot math.
        :param identity_server_octet_base: server last-octet base override.
        :param identity_server_subnet: server.address subnet override.
        :param identity_dimensions: fixed dashboard/filter dimensions for every sample.
        :return: list of MetricSample objects, including the scrape-health samples.
        """
        started_at = exporter.time.monotonic()
        identity_options = exporter.resolve_identity_options(
            host_prefix=identity_host_prefix,
            bmc_octet_base=identity_bmc_octet_base,
            server_octet_base=identity_server_octet_base,
            server_subnet=identity_server_subnet,
            extra_dimensions=identity_dimensions,
        )
        identity = build_identity_dimensions(
            label_bmc_ip or self.idrac_ip,
            vendor=self._vendor_label(vendor),
            **identity_options,
        )
        environment_rows = self._environment_command_rows(do_async=do_async)
        thermal_rows = self._thermal_rows(do_async=do_async, do_expanded=do_expanded)
        sensor_rows = self._invoke_rows(ApiRequestType.Sensors, "sensors",
                                        do_async=do_async, do_expanded=do_expanded)
        nvlink_rows = self._invoke_rows(ApiRequestType.NvLinkPorts, "nvlink-ports",
                                        do_async=do_async, do_expanded=do_expanded)
        metric_rows = self._invoke_rows(ApiRequestType.MetricReports, "metric-reports",
                                        do_async=do_async, do_expanded=do_expanded)
        leak_rows = self._leak_detection_rows(do_async=do_async)
        network_rows = self._invoke_rows(ApiRequestType.NetworkAdapters, "network-adapters",
                                         do_async=do_async, do_expanded=do_expanded)
        component_rows = self._invoke_rows(ApiRequestType.ComponentIntegrity, "component-integrity",
                                           do_async=do_async, do_expanded=do_expanded)
        samples = build_metric_samples(
            identity=identity,
            environment_rows=environment_rows,
            sensor_rows=sensor_rows,
            nvlink_rows=nvlink_rows,
            metric_report_rows=metric_rows,
            thermal_rows=thermal_rows,
            leak_detection_rows=leak_rows,
            network_rows=network_rows,
            component_integrity_rows=component_rows,
        )
        samples.extend(exporter.scrape_health_samples(
            identity,
            ok=bool(samples),
            duration_seconds=exporter.time.monotonic() - started_at,
        ))
        return samples

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                listen: Optional[str] = "0.0.0.0",
                port: Optional[int] = 9109,
                interval: Optional[float] = 30.0,
                once: Optional[bool] = False,
                exporter_output: Optional[str] = "prometheus",
                label_bmc_ip: Optional[str] = None,
                vendor: Optional[str] = None,
                exporter_config_file: Optional[str] = None,
                push_signalfx: Optional[bool] = False,
                signalfx_ingest_url: Optional[str] = None,
                signalfx_token_env: Optional[str] = None,
                signalfx_token: Optional[str] = None,
                signalfx_token_file: Optional[str] = None,
                verify_readback: Optional[bool] = False,
                signalfx_realm: Optional[str] = None,
                signalfx_api_token_env: Optional[str] = None,
                identity_host_prefix: Optional[str] = None,
                identity_bmc_octet_base: Optional[int] = None,
                identity_server_octet_base: Optional[int] = None,
                identity_server_subnet: Optional[str] = None,
                identity_dimensions: Optional[list[str]] = None,
                otlp_endpoint: Optional[str] = None,
                otlp_protocol: Optional[str] = None,
                **kwargs) -> CommandResult:
        """Scrape once, serve Prometheus, or push SignalFx/OTLP datapoints.

        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: when True, issue the Redfish scrape queries asynchronously.
        :param do_expanded: when True, issue expanded ($expand) scrape queries.
        :param listen: address for the Prometheus /metrics listener.
        :param port: port for the Prometheus /metrics listener.
        :param interval: scrape interval in seconds for the long-running push/serve loops.
        :param once: scrape a single time and return the rendered output instead of serving.
        :param exporter_output: output format — ``prometheus``, ``signalfx``, or ``otlp``.
        :param label_bmc_ip: BMC IP used only for metric dimensions when it differs from the
            configured address.
        :param vendor: vendor dimension override; auto-detected when None.
        :param exporter_config_file: JSON config spec for SignalFx and identity settings.
        :param push_signalfx: when True, push SignalFx datapoints instead of serving Prometheus.
        :param signalfx_ingest_url: SignalFx ingest URL; resolved from the environment when None.
        :param signalfx_token_env: environment variable holding the SignalFx ingest token.
        :param signalfx_token: direct SignalFx ingest token value.
        :param signalfx_token_file: file containing the SignalFx ingest token.
        :param verify_readback: when True, a --once SignalFx push reads the metric
            time series back from Splunk MTS and returns a compact canary result;
            a POST returning 200 is not treated as proof of ingestion.
        :param signalfx_realm: Splunk Observability realm for readback; resolved
            from SPLUNK_O11Y_REALM when None.
        :param signalfx_api_token_env: env var holding the Splunk API (read) token
            for readback; defaults to SPLUNK_API_TOKEN.
        :param identity_host_prefix: host.name prefix override.
        :param identity_bmc_octet_base: BMC last-octet base override for slot math.
        :param identity_server_octet_base: server last-octet base override.
        :param identity_server_subnet: server.address subnet override.
        :param identity_dimensions: fixed dashboard/filter dimensions for every sample.
        :param otlp_endpoint: OTLP collector endpoint for ``--output otlp``; resolved from
            OTEL_* env when None.
        :param otlp_protocol: OTLP transport (``grpc`` or ``http/protobuf``); resolved from
            OTEL_* env when None.
        :return: on ``once``, a CommandResult wrapping the rendered/pushed output and a
            sample-count summary; a CommandResult with empty payload when serving or looping
            forever.
        """
        config_options = exporter.exporter_config_options(exporter_config_file)

        def option(name, value):
            """Return the explicit value or the config value for ``name``.

            :param name: flattened exporter config option name.
            :param value: explicit CLI or programmatic value.
            :return: explicit value when set, else the config value.
            """
            return value if value not in (None, "") else config_options.get(name)

        signalfx_ingest_url = option("signalfx_ingest_url", signalfx_ingest_url)
        signalfx_token_env = option("signalfx_token_env", signalfx_token_env)
        signalfx_token = option("signalfx_token", signalfx_token)
        signalfx_token_file = option("signalfx_token_file", signalfx_token_file)
        identity_host_prefix = option("identity_host_prefix", identity_host_prefix)
        identity_bmc_octet_base = option(
            "identity_bmc_octet_base", identity_bmc_octet_base)
        identity_server_octet_base = option(
            "identity_server_octet_base", identity_server_octet_base)
        identity_server_subnet = option(
            "identity_server_subnet", identity_server_subnet)
        identity_dimensions = option("identity_dimensions", identity_dimensions)

        def collect_current_samples():
            """Collect samples with the resolved exporter identity options.

            :return: list of MetricSample objects for the current scrape.
            """
            return self.collect_samples(
                label_bmc_ip,
                vendor,
                do_async,
                do_expanded,
                identity_host_prefix=identity_host_prefix,
                identity_bmc_octet_base=identity_bmc_octet_base,
                identity_server_octet_base=identity_server_octet_base,
                identity_server_subnet=identity_server_subnet,
                identity_dimensions=identity_dimensions,
            )

        if exporter_output == "otlp":
            from . import otlp
            if once:
                samples = collect_current_samples()
                result = otlp.push_otlp(
                    samples, endpoint=otlp_endpoint, protocol=otlp_protocol)
                return CommandResult(
                    None, None,
                    {"sample_count": len(samples), "export_result": str(result)}, None)

            def scrape_samples():
                """Scrape one round of telemetry samples for the OTLP loop.

                :return: list of MetricSample objects for the current scrape.
                """
                return collect_current_samples()

            otlp.run_otlp_loop(
                scrape_samples, float(interval or 30.0),
                endpoint=otlp_endpoint, protocol=otlp_protocol)
            return CommandResult(None, None, None, None)

        if once:
            # Resolve and validate the push target BEFORE scraping so a missing
            # token or a bare (non-/v2/datapoint) ingest URL fails fast.
            if exporter_output == "signalfx" and push_signalfx:
                token = resolve_signalfx_token(
                    signalfx_token_env,
                    token=signalfx_token,
                    token_file=signalfx_token_file,
                )
                ingest_url = resolve_signalfx_ingest_url(signalfx_ingest_url)
                scrape_start = time.monotonic()
                samples = collect_current_samples()
                scrape_ms = int((time.monotonic() - scrape_start) * 1000)
                body = to_signalfx_body(samples)
                push_start = time.monotonic()
                status = exporter.push_signalfx(body, token, ingest_url)
                push_ms = int((time.monotonic() - push_start) * 1000)
                if not verify_readback:
                    return CommandResult(
                        body, None,
                        {"sample_count": len(samples),
                         "push_status": status,
                         "ingest_url": ingest_url},
                        None,
                    )
                # POST 200 is not proof: confirm the datapoints are visible in MTS.
                realm = signalfx_realm or os.environ.get("SPLUNK_O11Y_REALM", "")
                api_token = os.environ.get(
                    signalfx_api_token_env or "SPLUNK_API_TOKEN", "")
                if not realm or not api_token:
                    raise ValueError(
                        "--verify-readback needs a realm (--signalfx-realm or "
                        "SPLUNK_O11Y_REALM) and an API token (--signalfx-api-token-env "
                        "or SPLUNK_API_TOKEN)")
                metric_names = sorted({sample.metric for sample in samples})
                # Scope readback by the dimensions fixed across every sample, so
                # host identity and dashboard filters are proven without
                # accidentally including metric-specific gpu/sensor labels.
                dimensions = exporter.common_sample_dimensions(samples)
                readback_start = time.monotonic()
                readback = exporter.verify_signalfx_readback(
                    realm, api_token, metric_names, dimensions)
                readback_ms = int((time.monotonic() - readback_start) * 1000)
                summary, error = exporter.build_readback_result(
                    status, ingest_url, len(samples), metric_names, readback,
                    {"scrape": scrape_ms, "push": push_ms, "readback": readback_ms},
                    int(time.time() * 1000))
                return CommandResult(summary, None, summary, error)
            samples = collect_current_samples()
            data = (to_signalfx_body(samples) if exporter_output == "signalfx"
                    else render_prometheus_text(samples))
            return CommandResult(data, None, {"sample_count": len(samples)}, None)

        if push_signalfx or exporter_output == "signalfx":
            token = resolve_signalfx_token(
                signalfx_token_env,
                token=signalfx_token,
                token_file=signalfx_token_file,
            )
            ingest_url = resolve_signalfx_ingest_url(signalfx_ingest_url)

            def scrape_samples():
                """Scrape one round of telemetry samples for the SignalFx loop.

                :return: list of MetricSample objects for the current scrape.
                """
                return collect_current_samples()

            run_signalfx_loop(scrape_samples, token, ingest_url, float(interval or 30.0))
            return CommandResult(None, None, None, None)

        def scrape_text():
            """Scrape samples and render them as Prometheus exposition text.

            :return: the rendered Prometheus /metrics text for the current scrape.
            """
            samples = collect_current_samples()
            return render_prometheus_text(samples)

        serve_prometheus(scrape_text, listen or "0.0.0.0", int(port or 9109))
        return CommandResult(None, None, None, None)
