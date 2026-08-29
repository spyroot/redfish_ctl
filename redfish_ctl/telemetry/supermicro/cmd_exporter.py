"""Expose Supermicro/NV72 Redfish telemetry through a selected writer.

    redfish_ctl --vendor supermicro exporter
    redfish_ctl --vendor supermicro exporter --once --output prometheus

The command is deliberately an orchestrator.  The concrete reader owns every
Supermicro Redfish collection and mapping decision; the concrete writer owns
rendering, serving, or pushing the resulting samples.
"""

from __future__ import annotations

from typing import Callable, Mapping, Optional

from redfish_ctl.redfish_api_common import ApiRequestType, Singleton
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.supermico_manager import SupermicroManager
from redfish_ctl.telemetry import exporter
from redfish_ctl.telemetry.abstract_exporter_reader import AbstractExporterReader
from redfish_ctl.telemetry.abstract_exporter_writer import AbstractExporterWriter
from redfish_ctl.telemetry.exporter_cli import register_exporter_subcommand
from redfish_ctl.telemetry.metric_model import MetricDefinition
from redfish_ctl.telemetry.otlp import OtlpWriter
from redfish_ctl.telemetry.prometheus import PrometheusWriter
from redfish_ctl.telemetry.signalfx import SignalFxWriter
from redfish_ctl.telemetry.supermicro.super_microexporter import (
    SupermicroExporterReader,
)


class Exporter(SupermicroManager,
               scm_type=ApiRequestType.SupermicroExporter,
               name="exporter",
               metaclass=Singleton):
    """Connect one concrete Supermicro reader to one concrete output writer."""

    reader_cls = SupermicroExporterReader

    def __init__(self, *args, **kwargs):
        """Initialize the manager-backed reader; a writer is selected per invocation."""
        super().__init__(*args, **kwargs)
        self._reader: AbstractExporterReader = self.reader_cls(self)
        self._writer: Optional[AbstractExporterWriter] = None

    @staticmethod
    def register_subcommand(cls):
        """Register the vendor-scoped ``exporter`` subcommand.

        :return: parser, command name, and help text from the shared CLI builder.
        """
        return register_exporter_subcommand(cls)

    def collect_samples(self, **kwargs):
        """Compatibility facade that delegates one complete scrape to the reader.

        :return: writer-ready samples from the owned reader.
        """
        return self._reader.read(**kwargs)

    @staticmethod
    def _option(config_options: Mapping, name: str, value):
        """Return an explicit invocation value or its exporter-config fallback.

        :param config_options: options loaded from the exporter configuration.
        :param name: configuration option name.
        :param value: explicit invocation value.
        :return: explicit value when set, otherwise the configured fallback.
        """
        return value if value not in (None, "") else config_options.get(name)

    def _create_writer(
            self,
            *,
            exporter_output: str,
            once: bool,
            listen: str,
            port: int,
            interval: float,
            push_signalfx: bool,
            signalfx_ingest_url: Optional[str],
            signalfx_token_env: Optional[str],
            signalfx_token: Optional[str],
            signalfx_token_file: Optional[str],
            verify_readback: bool,
            readback_freshness_ms: int,
            signalfx_realm: Optional[str],
            signalfx_api_token_env: Optional[str],
            otlp_endpoint: Optional[str],
            otlp_protocol: Optional[str],
            service_name: Optional[str],
            definition_lookup: Callable[[str], MetricDefinition],
            ) -> AbstractExporterWriter:
        """Construct the one writer selected by this invocation.

        :param exporter_output: selected output backend name.
        :param once: whether the invocation emits one scrape and exits.
        :param listen: Prometheus listener address.
        :param port: Prometheus listener port.
        :param interval: seconds between continuous scrapes.
        :param push_signalfx: enable SignalFx push mode.
        :param signalfx_ingest_url: SignalFx ingest endpoint.
        :param signalfx_token_env: environment variable holding an ingest token.
        :param signalfx_token: explicit SignalFx ingest token.
        :param signalfx_token_file: file containing a SignalFx ingest token.
        :param verify_readback: verify SignalFx samples through API read-back.
        :param readback_freshness_ms: maximum read-back sample age in milliseconds.
        :param signalfx_realm: SignalFx realm used to derive endpoints.
        :param signalfx_api_token_env: environment variable holding an API token.
        :param otlp_endpoint: OTLP destination endpoint.
        :param otlp_protocol: OTLP transport protocol.
        :param service_name: service name attached to OTLP resources.
        :param definition_lookup: concrete reader's metric catalog resolver.
        :return: configured concrete writer.
        """
        if exporter_output == "otlp":
            return OtlpWriter(
                endpoint=otlp_endpoint,
                protocol=otlp_protocol,
                interval=interval,
                service_name=service_name or "redfish_ctl",
            )
        if exporter_output == "signalfx" or (push_signalfx and not once):
            return SignalFxWriter(
                ingest_url=signalfx_ingest_url,
                token=signalfx_token,
                token_env=signalfx_token_env,
                token_file=signalfx_token_file,
                realm=signalfx_realm,
                api_token_env=signalfx_api_token_env,
                verify_readback=verify_readback,
                freshness_ms=readback_freshness_ms,
                interval=interval,
                push=push_signalfx,
            )
        return PrometheusWriter(
            listen=listen,
            port=port,
            definition_lookup=definition_lookup,
        )

    def execute(
            self,
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
            **kwargs,
            ) -> CommandResult:
        """Resolve invocation options and connect this reader to one writer.

        :param filename: accepted shared output option; unused by the exporter.
        :param data_type: accepted shared serialization option; unused here.
        :param verbose: accepted shared verbosity option; unused here.
        :param do_async: issue collector reads through asynchronous paths.
        :param do_expanded: request expanded resources where collectors support it.
        :param listen: Prometheus listener address.
        :param port: Prometheus listener port.
        :param interval: seconds between continuous scrapes.
        :param once: emit one scrape and exit.
        :param exporter_output: selected writer backend.
        :param label_bmc_ip: explicit BMC identity label.
        :param vendor: requested vendor label; must be Supermicro when supplied.
        :param exporter_config_file: exporter configuration file path.
        :param push_signalfx: enable SignalFx push mode.
        :param signalfx_ingest_url: SignalFx ingest endpoint.
        :param signalfx_token_env: environment variable holding an ingest token.
        :param signalfx_token: explicit SignalFx ingest token.
        :param signalfx_token_file: file containing a SignalFx ingest token.
        :param verify_readback: verify SignalFx samples through API read-back.
        :param readback_freshness_seconds: maximum read-back sample age.
        :param signalfx_realm: SignalFx realm used to derive endpoints.
        :param signalfx_api_token_env: environment variable holding an API token.
        :param identity_host_prefix: optional normalized host prefix.
        :param identity_bmc_octet_base: first BMC address octet used for identity.
        :param identity_server_octet_base: first server octet used for identity.
        :param identity_server_subnet: subnet used to derive server identity.
        :param deployment_environment: canonical deployment environment label.
        :param deployment_environment_compat: compatibility environment label.
        :param require_deployment_environment: require an environment identity.
        :param extra_dimensions: additional fixed metric dimensions.
        :param service_name: telemetry service name.
        :param service_namespace: telemetry service namespace.
        :param service_instance_id: stable telemetry service instance UUID.
        :param service_version: telemetry service version.
        :param service_criticality: service criticality dimension.
        :param otlp_endpoint: OTLP destination endpoint.
        :param otlp_protocol: OTLP transport protocol.
        :param otlp_traces: enable trace emission while scraping.
        :return: one-shot writer result or an empty result after continuous mode.
        """
        del filename, data_type, verbose, kwargs
        config_options = exporter.exporter_config_options(exporter_config_file)
        def option(name, value):
            """Resolve one explicit value against the loaded configuration.

            :param name: configuration option name.
            :param value: explicit invocation value.
            :return: explicit or configured option value.
            """
            return self._option(config_options, name, value)

        signalfx_ingest_url = option("signalfx_ingest_url", signalfx_ingest_url)
        signalfx_token_env = option("signalfx_token_env", signalfx_token_env)
        signalfx_token = option("signalfx_token", signalfx_token)
        signalfx_token_file = option("signalfx_token_file", signalfx_token_file)
        readback_freshness_seconds = option(
            "readback_freshness_seconds", readback_freshness_seconds)
        freshness_seconds = (
            900.0 if readback_freshness_seconds in (None, "")
            else float(readback_freshness_seconds)
        )
        readback_freshness_ms = int(freshness_seconds * 1000)
        if readback_freshness_ms <= 0:
            raise ValueError("--readback-freshness-seconds must be greater than 0")

        reader_options = {
            "label_bmc_ip": label_bmc_ip,
            "vendor": vendor,
            "do_async": bool(do_async),
            "do_expanded": bool(do_expanded),
            "identity_host_prefix": option("identity_host_prefix", identity_host_prefix),
            "identity_bmc_octet_base": option(
                "identity_bmc_octet_base", identity_bmc_octet_base),
            "identity_server_octet_base": option(
                "identity_server_octet_base", identity_server_octet_base),
            "identity_server_subnet": option(
                "identity_server_subnet", identity_server_subnet),
            "deployment_environment": option(
                "deployment_environment", deployment_environment),
            "deployment_environment_compat": option(
                "deployment_environment_compat", deployment_environment_compat),
            "require_deployment_environment": option(
                "require_deployment_environment", require_deployment_environment),
            "extra_dimensions": option("extra_dimensions", extra_dimensions),
            "service_name": option("service_name", service_name),
            "service_namespace": option("service_namespace", service_namespace),
            "service_instance_id": option("service_instance_id", service_instance_id),
            "service_version": option("service_version", service_version),
            "service_criticality": option("service_criticality", service_criticality),
            "otlp_traces": bool(otlp_traces),
        }

        writer = self._create_writer(
            exporter_output=exporter_output or "prometheus",
            once=bool(once),
            listen=listen or "0.0.0.0",
            port=int(port or 9109),
            interval=float(interval or 30.0),
            push_signalfx=bool(push_signalfx),
            signalfx_ingest_url=signalfx_ingest_url,
            signalfx_token_env=signalfx_token_env,
            signalfx_token=signalfx_token,
            signalfx_token_file=signalfx_token_file,
            verify_readback=bool(verify_readback),
            readback_freshness_ms=readback_freshness_ms,
            signalfx_realm=signalfx_realm,
            signalfx_api_token_env=signalfx_api_token_env,
            otlp_endpoint=otlp_endpoint,
            otlp_protocol=otlp_protocol,
            service_name=reader_options["service_name"],
            definition_lookup=self._reader.metric_definition,
        )
        self._writer = writer
        def scrape():
            """Collect one writer-ready scrape through the owned reader.

            :return: concrete reader samples.
            """
            return self._reader.read(**reader_options)

        if once:
            return writer.write_once(scrape())
        writer.run(scrape)
        return CommandResult(None, None, None, None)
