"""Expose Redfish telemetry as Prometheus or SignalFx metrics.

    redfish_ctl exporter
    redfish_ctl exporter --once --output prometheus
    redfish_ctl exporter --once --output signalfx

The exporter is read-only. It walks modern Redfish telemetry resources and
normalizes them into the ``hw.*`` metric contract used by the GB300/NV72
observability demo.
"""
import logging
from abc import abstractmethod
from collections.abc import Callable, Mapping
from typing import Optional

from redfish_ctl import SupermicroManager
from redfish_ctl.cmd_exceptions import ResourceNotFound
from redfish_ctl.idrac_shared import ApiRequestType, Singleton
from redfish_ctl.redfish_manager import CommandResult, RedfishResponseCache
from redfish_ctl.telemetry import exporter
from redfish_ctl.telemetry.exporter_cli import register_exporter_subcommand
from redfish_ctl.telemetry.exporter import build_telemetry_identity
from redfish_ctl.telemetry.otlp import OtlpWriter
from redfish_ctl.telemetry.prometheus import PrometheusWriter
from redfish_ctl.telemetry.signalfx import SignalFxWriter
from redfish_ctl.telemetry.supermicro.super_microexporter import SupermicroExporterReader

logger = logging.getLogger(__name__)


class Exporter(SupermicroManager,
               scm_type=ApiRequestType.SupermicroExporter,
               name='exporter',
               metaclass=Singleton):
    """Read BMC telemetry and expose Prometheus or SignalFx metric output."""

    # The concrete reader is bound here (registration) and constructed in
    # __init__; the command depends only on the AbstractExporterReader contract.
    reader_cls = SupermicroExporterReader

    _UNSUPPORTED_COLLECTOR_ERRORS = {
        "FailedDiscoverAction",
        "MissingResource",
        "RedfishMethodNotAllowed",
        "RedfishNotFound",
        "ResourceNotFound",
        "UnsupportedAction",
    }
    _AUTHENTICATION_ERRORS = {
        "AuthenticationFailed",
        "RedfishForbidden",
        "RedfishUnauthorized",
    }

    def __init__(self, *args, **kwargs):
        """Initialize the exporter command and its telemetry reader."""
        super(Exporter, self).__init__(*args, **kwargs)
        self._reader = self.reader_cls()

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``exporter`` subcommand.

        :param cls: the command class, used for its :meth:`base_parser` factory.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        return register_exporter_subcommand(cls)



    @classmethod
    def _is_unsupported_collector_error(cls, exc: Exception) -> bool:
        """Return whether ``exc`` means the optional collector is unsupported.

        :param exc: exception raised by a collector command.
        :return: True when the collector should be counted unsupported, not failed.
        """
        return exc.__class__.__name__ in cls._UNSUPPORTED_COLLECTOR_ERRORS

    @classmethod
    def _collector_error_kind(cls, exc: Exception) -> str:
        """Map a collector exception to the bounded exporter error-kind label.

        :param exc: exception raised while collecting or validating rows.
        :return: one of the allowed exporter error-kind labels.
        """
        name = exc.__class__.__name__
        lowered = name.lower()
        if isinstance(exc, TimeoutError) or "timeout" in lowered:
            return "timeout"
        if name in cls._AUTHENTICATION_ERRORS or "unauthoriz" in lowered:
            return "authentication"
        if "jsondecode" in lowered or "decode" in lowered:
            return "decode_error"
        if name in {"TypeError", "ValueError", "KeyError", "UnexpectedResponse"}:
            return "invalid_payload"
        if "http" in lowered or "redfish" in lowered or "requestfailed" in lowered:
            return "http_error"
        return "internal"

    @staticmethod
    def _validate_collector_rows(rows) -> tuple[Mapping, ...]:
        """Return rows as an immutable tuple after shape validation.

        :param rows: candidate iterable of mapping rows.
        :return: tuple of mapping rows.
        :raises ValueError: when the payload is not a row list.
        """
        if isinstance(rows, (str, bytes, dict)) or not hasattr(rows, "__iter__"):
            raise ValueError("collector returned a non-list payload")
        normalized = tuple(rows)
        if not all(isinstance(row, Mapping) for row in normalized):
            raise ValueError("collector returned a non-mapping row")
        return normalized

    @staticmethod
    def _extract_list_rows(data) -> list:
        """Extract rows from collectors that return a list payload.

        :param data: command result payload.
        :return: list payload.
        :raises ValueError: when the payload is not a list.
        """
        if not isinstance(data, list):
            raise ValueError("collector returned a non-list payload")
        return data

    @staticmethod
    def _extract_environment_rows(data) -> list:
        """Extract rows from the environment-metrics command payload.

        :param data: command result payload.
        :return: list of EnvironmentMetrics rows.
        :raises ValueError: when the payload has an unexpected shape.
        """
        if isinstance(data, dict) and isinstance(data.get("metrics"), list):
            return data["metrics"]
        if isinstance(data, list):
            return data
        raise ValueError("environment-metrics returned an unexpected payload")

    @staticmethod
    def _extract_leak_detector_rows(data) -> list:
        """Extract rows from the leak-detectors command payload.

        :param data: command result payload.
        :return: list of leak-detector rows.
        :raises ValueError: when the payload has an unexpected shape.
        """
        if isinstance(data, dict) and isinstance(data.get("detectors"), list):
            return data["detectors"]
        raise ValueError("leak-detectors returned an unexpected payload")

    @staticmethod
    def _extract_thermal_rows(data) -> list:
        """Extract rows from the thermal command payload.

        :param data: command result payload.
        :return: list of temperature-reading rows.
        :raises ValueError: when the payload has an unexpected shape.
        """
        if isinstance(data, dict) and isinstance(data.get("temperature_readings"), list):
            return data["temperature_readings"]
        raise ValueError("thermal returned an unexpected payload")

    def _collect_result(self,
                        collector: str,
                        call: Callable[[], CommandResult],
                        extract_rows: Callable[[object], list]) -> exporter.CollectorResult:
        """Run one collector command and preserve its health classification.

        :param collector: stable collector name used in exporter self-telemetry.
        :param call: callable that invokes the collector command.
        :param extract_rows: callable that extracts the row list from command data.
        :return: CollectorResult for the collector.
        """
        started_at = exporter.time.monotonic()
        try:
            result = call()
        except Exception as exc:
            duration = exporter.time.monotonic() - started_at
            if self._is_unsupported_collector_error(exc):
                return exporter.CollectorResult(
                    collector, False, True, duration, (), None)
            return exporter.CollectorResult(
                collector, True, False, duration, (),
                self._collector_error_kind(exc))
        duration = exporter.time.monotonic() - started_at
        if not hasattr(result, "data"):
            return exporter.CollectorResult(
                collector, True, False, duration, (), "invalid_payload")
        if getattr(result, "error", None):
            return exporter.CollectorResult(
                collector, True, False, duration, (), "internal")
        try:
            rows = self._validate_collector_rows(extract_rows(result.data))
        except Exception as exc:
            return exporter.CollectorResult(
                collector, True, False, duration, (),
                self._collector_error_kind(exc))
        return exporter.CollectorResult(collector, True, True, duration, rows, None)

    def _invoke_collector(self,
                          api_type: ApiRequestType,
                          name: str,
                          extract_rows: Callable[[object], list],
                          redfish_cache: Optional[RedfishResponseCache] = None,
                          **kwargs) -> exporter.CollectorResult:
        """Invoke a registered read-only collector and return its result model.

        :param api_type: ApiRequestType of the collector command.
        :param name: registered command name and collector label.
        :param extract_rows: callable that extracts the collector row list.
        :param redfish_cache: optional per-scrape cache shared by collectors.
        :return: CollectorResult for the collector.
        """
        return self._collect_result(
            name,
            lambda: self.sync_invoke(
                api_type,
                name,
                redfish_cache=redfish_cache,
                **kwargs,
            ),
            extract_rows,
        )


    def _vendor_label(self,
                      vendor: Optional[str],
                      redfish_cache: Optional[RedfishResponseCache] = None) -> str:
        """Return a stable lower-case vendor label.

        :param vendor: explicit vendor override; when falsy the vendor is auto-detected.
        :param redfish_cache: optional per-scrape cache for the ServiceRoot read.
        :return: the vendor label, or ``"unknown"`` when neither is available.
        """
        if vendor:
            return vendor
        try:
            if redfish_cache is None:
                detected = self.redfish_vendor
            else:
                service_root = self.base_query(
                    "/redfish/v1/", redfish_cache=redfish_cache).data
                detected = service_root.get("Vendor", "") \
                    if isinstance(service_root, dict) else ""
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
                        deployment_environment: Optional[str] = None,
                        deployment_environment_compat: Optional[str] = None,
                        require_deployment_environment: Optional[bool] = None,
                        extra_dimensions: Optional[Mapping | list[str]] = None,
                        service_name: Optional[str] = None,
                        service_namespace: Optional[str] = None,
                        service_instance_id: Optional[str] = None,
                        service_version: Optional[str] = None,
                        service_criticality: Optional[str] = None,
                        otlp_traces: bool = False) -> list:
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
        :param deployment_environment: deployment environment join dimension.
        :param deployment_environment_compat: deployment environment key compatibility mode.
        :param require_deployment_environment: when True, fail if the environment is absent.
        :param extra_dimensions: fixed validated dimensions applied to every sample.
        :param service_name: OTel service.name (logical service name) emitted on every sample.
        :param service_namespace: optional OTel service namespace resource attribute.
        :param service_instance_id: optional stable process identity override.
        :param service_version: optional service component version resource attribute.
        :param service_criticality: optional service importance resource attribute.
        :param otlp_traces: whether to configure tracing with this resolved identity.
        :return: list of MetricSample objects, including the scrape-health samples.
        """
        started_at = exporter.time.monotonic()
        redfish_cache = RedfishResponseCache()
        identity_options = exporter.resolve_identity_options(
            host_prefix=identity_host_prefix,
            bmc_octet_base=identity_bmc_octet_base,
            server_octet_base=identity_server_octet_base,
            server_subnet=identity_server_subnet,
            deployment_environment=deployment_environment,
            deployment_environment_compat=deployment_environment_compat,
            require_deployment_environment=require_deployment_environment,
            extra_dimensions=extra_dimensions,
            service_name=service_name,
            service_namespace=service_namespace,
            service_instance_id=service_instance_id,
            service_version=service_version,
            service_criticality=service_criticality,
        )
        if identity_options["service_instance_id"] is None:
            identity_options["service_instance_id"] = (
                self._default_service_instance_id(
                    redfish_cache,
                    do_async=do_async,
                )
            )
        telemetry_identity = build_telemetry_identity(
            label_bmc_ip or self.idrac_ip,
            vendor=self._vendor_label(vendor, redfish_cache=redfish_cache),
            **identity_options,
        )
        if otlp_traces:
            from . import tracing
            tracing.setup_otlp(
                telemetry_identity.service_name,
                telemetry_identity.resource_attributes(),
            )
        identity = telemetry_identity.dimensions()
        collector_results = [
            self._invoke_collector(
                ApiRequestType.EnvironmentMetrics,
                "environment-metrics",
                self._extract_environment_rows,
                redfish_cache=redfish_cache,
                do_async=do_async,
            ),
            self._invoke_collector(
                ApiRequestType.Thermal,
                "thermal",
                self._extract_thermal_rows,
                redfish_cache=redfish_cache,
                do_async=do_async,
                do_expanded=do_expanded,
            ),
            self._invoke_collector(
                ApiRequestType.Sensors,
                "sensors",
                self._extract_list_rows,
                redfish_cache=redfish_cache,
                do_async=do_async,
                do_expanded=do_expanded,
            ),
            self._invoke_collector(
                ApiRequestType.NvLinkPorts,
                "nvlink-ports",
                self._extract_list_rows,
                redfish_cache=redfish_cache,
                do_async=do_async,
                do_expanded=do_expanded,
            ),
            self._invoke_collector(
                ApiRequestType.SupermicroMetricReports,
                "metric-reports",
                self._extract_list_rows,
                redfish_cache=redfish_cache,
                do_async=do_async,
                do_expanded=do_expanded,
            ),
            self._invoke_collector(
                ApiRequestType.LeakDetectors,
                "leak-detectors",
                self._extract_leak_detector_rows,
                redfish_cache=redfish_cache,
                do_async=do_async,
            ),
            self._invoke_collector(
                ApiRequestType.NetworkAdapters,
                "network-adapters",
                self._extract_list_rows,
                redfish_cache=redfish_cache,
                do_async=do_async,
                do_expanded=do_expanded,
            ),
            self._invoke_collector(
                ApiRequestType.ComponentIntegrity,
                "component-integrity",
                self._extract_list_rows,
                redfish_cache=redfish_cache,
                do_async=do_async,
                do_expanded=do_expanded,
            ),
        ]
        rows_by_collector = {
            result.name: result.rows
            for result in collector_results
        }
        samples = self._reader.build_metric_samples(
            identity=identity,
            environment_rows=rows_by_collector["environment-metrics"],
            sensor_rows=rows_by_collector["sensors"],
            nvlink_rows=rows_by_collector["nvlink-ports"],
            metric_report_rows=rows_by_collector["metric-reports"],
            thermal_rows=rows_by_collector["thermal"],
            leak_detection_rows=rows_by_collector["leak-detectors"],
            network_rows=rows_by_collector["network-adapters"],
            component_integrity_rows=rows_by_collector["component-integrity"],
        )
        scrape_ok, scrape_partial = exporter.collector_scrape_status(collector_results)
        samples.extend(exporter.scrape_health_samples(
            identity,
            ok=scrape_ok,
            duration_seconds=exporter.time.monotonic() - started_at,
            collector_results=collector_results,
            partial=scrape_partial,
            timestamp_seconds=exporter.time.time(),
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
                readback_freshness_seconds: Optional[float] = 900.0,
                signalfx_realm: Optional[str] = None,
                signalfx_api_token_env: Optional[str] = None,
                identity_host_prefix: Optional[str] = None,
                identity_bmc_octet_base: Optional[int] = None,
                identity_server_octet_base: Optional[int] = None,
                identity_server_subnet: Optional[str] = None,
                deployment_environment: Optional[str] = None,
                deployment_environment_compat: Optional[str] = None,
                require_deployment_environment: Optional[bool] = None,
                extra_dimensions: Optional[list[str]] = None,
                service_name: Optional[str] = None,
                service_namespace: Optional[str] = None,
                service_instance_id: Optional[str] = None,
                service_version: Optional[str] = None,
                service_criticality: Optional[str] = None,
                otlp_endpoint: Optional[str] = None,
                otlp_protocol: Optional[str] = None,
                otlp_traces: bool = False,
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
        :param readback_freshness_seconds: freshness window for readback verdicts.
        :param signalfx_realm: Splunk Observability realm for readback; resolved
            from SPLUNK_O11Y_REALM when None.
        :param signalfx_api_token_env: env var holding the Splunk API (read) token
            for readback; defaults to SPLUNK_API_TOKEN.
        :param identity_host_prefix: host.name prefix override.
        :param identity_bmc_octet_base: BMC last-octet base override for slot math.
        :param identity_server_octet_base: server last-octet base override.
        :param identity_server_subnet: server.address subnet override.
        :param deployment_environment: deployment environment join dimension.
        :param deployment_environment_compat: deployment environment key compatibility mode.
        :param require_deployment_environment: when True, fail if the environment is absent.
        :param extra_dimensions: fixed validated dimensions applied to every sample.
        :param service_name: OTel service.name (logical service name) emitted on every
            series; defaults to 'redfish_ctl'.
        :param service_namespace: optional OTel service namespace resource attribute.
        :param service_instance_id: optional stable exporter-process identity override.
        :param service_version: optional service component version resource attribute.
        :param service_criticality: optional service importance resource attribute.
        :param otlp_endpoint: OTLP collector endpoint for ``--output otlp``; resolved from
            OTEL_* env when None.
        :param otlp_protocol: OTLP transport (``grpc`` or ``http/protobuf``); resolved from
            OTEL_* env when None.
        :param otlp_traces: whether to configure tracing with the resolved producer identity.
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
        readback_freshness_seconds = option(
            "readback_freshness_seconds", readback_freshness_seconds)
        freshness_seconds = (
            900.0
            if readback_freshness_seconds in (None, "")
            else float(readback_freshness_seconds)
        )
        readback_freshness_ms = int(freshness_seconds * 1000)
        if readback_freshness_ms <= 0:
            raise ValueError("--readback-freshness-seconds must be greater than 0")
        identity_host_prefix = option("identity_host_prefix", identity_host_prefix)
        identity_bmc_octet_base = option(
            "identity_bmc_octet_base", identity_bmc_octet_base)
        identity_server_octet_base = option(
            "identity_server_octet_base", identity_server_octet_base)
        identity_server_subnet = option(
            "identity_server_subnet", identity_server_subnet)
        deployment_environment = option(
            "deployment_environment", deployment_environment)
        deployment_environment_compat = option(
            "deployment_environment_compat", deployment_environment_compat)
        require_deployment_environment = option(
            "require_deployment_environment", require_deployment_environment)
        extra_dimensions = option("extra_dimensions", extra_dimensions)
        service_name = option("service_name", service_name)
        service_namespace = option("service_namespace", service_namespace)
        service_instance_id = option("service_instance_id", service_instance_id)
        service_version = option("service_version", service_version)
        service_criticality = option("service_criticality", service_criticality)

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
                deployment_environment=deployment_environment,
                deployment_environment_compat=deployment_environment_compat,
                require_deployment_environment=require_deployment_environment,
                extra_dimensions=extra_dimensions,
                service_name=service_name,
                service_namespace=service_namespace,
                service_instance_id=service_instance_id,
                service_version=service_version,
                service_criticality=service_criticality,
                otlp_traces=otlp_traces,
            )

        # Build the concrete writer for --output and delegate: the writer owns
        # its backend config and emission (render/serve, push, readback, loop).
        # once selects the writer by output; long-running also honors
        # --push-signalfx; push controls once's POST-vs-body for SignalFx.
        if exporter_output == "otlp":
            writer = OtlpWriter(
                endpoint=otlp_endpoint, protocol=otlp_protocol,
                interval=float(interval or 30.0))
        elif exporter_output == "signalfx" or (push_signalfx and not once):
            writer = SignalFxWriter(
                ingest_url=signalfx_ingest_url,
                token=signalfx_token,
                token_env=signalfx_token_env,
                token_file=signalfx_token_file,
                realm=signalfx_realm,
                api_token_env=signalfx_api_token_env,
                verify_readback=bool(verify_readback),
                freshness_ms=readback_freshness_ms,
                interval=float(interval or 30.0),
                push=bool(push_signalfx))
        else:
            writer = PrometheusWriter(
                listen=listen or "0.0.0.0", port=int(port or 9109))

        if once:
            return writer.write_once(collect_current_samples())
        writer.run(collect_current_samples)
        return CommandResult(None, None, None, None)
