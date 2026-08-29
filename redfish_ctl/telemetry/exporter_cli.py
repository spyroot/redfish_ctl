"""Shared flags for vendor-scoped exporters, currently used by Supermicro.

The output, identity, and OpenTelemetry options are independent of the concrete
reader even though only the Supermicro exporter is currently registered.

Author Mus spyroot@gmail.com
"""


def register_exporter_subcommand(cls):
    """Build the shared ``exporter`` subcommand parser.

    :param cls: the command class, used for its :meth:`base_parser` factory.
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
        help="BMC IP used only for metric dimensions when different from REDFISH_IP")
    # The vendor dimension is the global ``--vendor`` router (see redfish_main):
    # it both routes the verb to the vendor manager and labels the metric vendor.
    cmd_parser.add_argument(
        "--credential-file", dest="exporter_credential_file", default=None, type=str,
        help="gitignored KEY=VALUE runtime file for "
             "the canonical REDFISH_IP/USERNAME/PASSWORD/PORT keys")
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
        "--readback-freshness-seconds", dest="readback_freshness_seconds",
        default=900.0, type=float,
        help="freshness window for --verify-readback, in seconds; default 900")
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
        "--deployment-environment", dest="deployment_environment",
        default=None, type=str,
        help="deployment environment join dimension, e.g. production or nv72-gb300")
    cmd_parser.add_argument(
        "--deployment-environment-compat", dest="deployment_environment_compat",
        default=None, choices=("both", "deprecated", "stable"),
        help="emit deployment.environment, deployment.environment.name, or both")
    cmd_parser.add_argument(
        "--require-deployment-environment", dest="require_deployment_environment",
        action="store_true", default=None,
        help="fail startup when no deployment environment is configured")
    cmd_parser.add_argument(
        "--dimension", dest="extra_dimensions", action="append", default=None,
        help="fixed validated KEY=VALUE dimension to add to every telemetry sample")
    cmd_parser.add_argument(
        "--service-name", dest="service_name", default=None, type=str,
        help="OTel producer service.name, distinct from deployment.environment; "
             "emitted on every series and the OTLP resource, default 'redfish_ctl'. "
             "Also REDFISH_EXPORTER_SERVICE_NAME")
    cmd_parser.add_argument(
        "--service-namespace", dest="service_namespace", default=None, type=str,
        help="optional OTel producer service.namespace resource attribute; distinct "
             "from deployment.environment. Also REDFISH_EXPORTER_SERVICE_NAMESPACE")
    cmd_parser.add_argument(
        "--service-instance-id", dest="service_instance_id", default=None, type=str,
        help="stable identity for this one exporter process, not a per-BMC metric "
             "label; raw tokens become UUIDv5. Set a unique override for each "
             "parallel process scraping the same BMC. Also "
             "REDFISH_EXPORTER_SERVICE_INSTANCE_ID")
    cmd_parser.add_argument(
        "--service-version", dest="service_version", default=None, type=str,
        help="optional OTel producer service.version resource attribute. Also "
             "REDFISH_EXPORTER_SERVICE_VERSION")
    cmd_parser.add_argument(
        "--service-criticality", dest="service_criticality", default=None, type=str,
        help="optional OTel service.criticality resource attribute; registered values "
             "include critical, high, medium, and low. Also "
             "REDFISH_EXPORTER_SERVICE_CRITICALITY")
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
