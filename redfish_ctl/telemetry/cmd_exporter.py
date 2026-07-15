"""Expose Redfish telemetry as Prometheus or SignalFx metrics.

    redfish_ctl exporter
    redfish_ctl exporter --once --output prometheus
    redfish_ctl exporter --once --output signalfx

The exporter is read-only. It walks modern Redfish telemetry resources and
normalizes them into the ``hw.*`` metric contract used by the GB300/NV72
observability demo.
"""
from abc import abstractmethod
from typing import Optional

from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import REDFISH_API, ApiRequestType, Singleton
from ..redfish_manager import CommandResult
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
        super(Exporter, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``exporter`` subcommand."""
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
            "--push-signalfx", action="store_true", default=False,
            help="push SignalFx datapoints instead of returning/serving Prometheus output")
        cmd_parser.add_argument(
            "--signalfx-ingest-url", dest="signalfx_ingest_url", default=None, type=str,
            help="SignalFx ingest URL; defaults to SPLUNK_INGEST_URL when pushing")
        cmd_parser.add_argument(
            "--signalfx-token-env", dest="signalfx_token_env", default="SPLUNK_ACCESS_TOKEN",
            type=str, help="environment variable that holds the SignalFx ingest token")
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
        """Return @odata.id strings from a Redfish collection."""
        if not isinstance(data, dict):
            return []
        return [m["@odata.id"] for m in data.get("Members", [])
                if isinstance(m, dict) and isinstance(m.get("@odata.id"), str)]

    def _invoke_rows(self, api_type: ApiRequestType, name: str, **kwargs) -> list:
        """Invoke another read-only command and tolerate absent resources."""
        try:
            result = self.sync_invoke(api_type, name, **kwargs)
        except Exception:
            return []
        return result.data if isinstance(result.data, list) else []

    def _invoke_dict(self, api_type: ApiRequestType, name: str, **kwargs) -> dict:
        """Invoke another read-only command that returns an object payload."""
        try:
            result = self.sync_invoke(api_type, name, **kwargs)
        except Exception:
            return {}
        return result.data if isinstance(result.data, dict) else {}

    def _leak_detection_rows(self, do_async: bool = False) -> list[dict]:
        """Return LeakDetector rows from the read-only leak-detectors command."""
        data = self._invoke_dict(
            ApiRequestType.LeakDetectors,
            "leak-detectors",
            do_async=do_async,
        )
        detectors = data.get("detectors") if isinstance(data, dict) else None
        return detectors if isinstance(detectors, list) else []

    def _environment_rows(self, do_async: bool = False) -> list[dict]:
        """Walk Chassis EnvironmentMetrics links and return their payloads."""
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
        """Return temperature readings from the read-only thermal command."""
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
        """Return normalized rows from the environment-metrics command."""
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
        """Return a stable lower-case vendor label."""
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
                        do_expanded: bool = False) -> list:
        """Scrape all supported read-only telemetry paths and build samples."""
        started_at = exporter.time.monotonic()
        identity = build_identity_dimensions(
            label_bmc_ip or self.idrac_ip,
            vendor=self._vendor_label(vendor),
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
                push_signalfx: Optional[bool] = False,
                signalfx_ingest_url: Optional[str] = None,
                signalfx_token_env: Optional[str] = "SPLUNK_ACCESS_TOKEN",
                otlp_endpoint: Optional[str] = None,
                otlp_protocol: Optional[str] = None,
                **kwargs) -> CommandResult:
        """Scrape once, serve Prometheus, or push SignalFx/OTLP datapoints."""
        if exporter_output == "otlp":
            from . import otlp
            if once:
                samples = self.collect_samples(label_bmc_ip, vendor, do_async, do_expanded)
                result = otlp.push_otlp(
                    samples, endpoint=otlp_endpoint, protocol=otlp_protocol)
                return CommandResult(
                    None, None,
                    {"sample_count": len(samples), "export_result": str(result)}, None)

            def scrape_samples():
                return self.collect_samples(label_bmc_ip, vendor, do_async, do_expanded)

            otlp.run_otlp_loop(
                scrape_samples, float(interval or 30.0),
                endpoint=otlp_endpoint, protocol=otlp_protocol)
            return CommandResult(None, None, None, None)

        if once:
            # Resolve and validate the push target BEFORE scraping so a missing
            # token or a bare (non-/v2/datapoint) ingest URL fails fast.
            if exporter_output == "signalfx" and push_signalfx:
                token = resolve_signalfx_token(signalfx_token_env)
                ingest_url = resolve_signalfx_ingest_url(signalfx_ingest_url)
                samples = self.collect_samples(label_bmc_ip, vendor, do_async, do_expanded)
                body = to_signalfx_body(samples)
                status = exporter.push_signalfx(body, token, ingest_url)
                return CommandResult(
                    body, None,
                    {"sample_count": len(samples),
                     "push_status": status,
                     "ingest_url": ingest_url},
                    None,
                )
            samples = self.collect_samples(label_bmc_ip, vendor, do_async, do_expanded)
            data = (to_signalfx_body(samples) if exporter_output == "signalfx"
                    else render_prometheus_text(samples))
            return CommandResult(data, None, {"sample_count": len(samples)}, None)

        if push_signalfx or exporter_output == "signalfx":
            token = resolve_signalfx_token(signalfx_token_env)
            ingest_url = resolve_signalfx_ingest_url(signalfx_ingest_url)

            def scrape_samples():
                return self.collect_samples(label_bmc_ip, vendor, do_async, do_expanded)

            run_signalfx_loop(scrape_samples, token, ingest_url, float(interval or 30.0))
            return CommandResult(None, None, None, None)

        def scrape_text():
            samples = self.collect_samples(label_bmc_ip, vendor, do_async, do_expanded)
            return render_prometheus_text(samples)

        serve_prometheus(scrape_text, listen or "0.0.0.0", int(port or 9109))
        return CommandResult(None, None, None, None)
