"""service.name (logical service name) emitted as a dimension by default.

The demo dashboard filters hw.* by service.name, so every exported series carries
service.name (default redfish_ctl) as a SignalFx/Prometheus dimension and the OTLP
resource attribute; --service-name / REDFISH_EXPORTER_SERVICE_NAME override it.

Author Mus spyroot@gmail.com
"""
import pytest

from redfish_ctl.telemetry import identity, otlp
from redfish_ctl.telemetry.cmd_exporter import Exporter


@pytest.fixture(autouse=True)
def _clear_service_env(monkeypatch):
    """Keep the service.name env vars out of a test's resolution.

    :param monkeypatch: pytest environment patcher.
    """
    for prefix in ("REDFISH_EXPORTER_", "IDRAC_EXPORTER_"):
        for suffix in (
            "SERVICE_NAME",
            "SERVICE_NAMESPACE",
            "SERVICE_INSTANCE_ID",
            "SERVICE_VERSION",
            "SERVICE_CRITICALITY",
        ):
            monkeypatch.delenv(prefix + suffix, raising=False)


def _identity(**kwargs):
    """Build a minimal TelemetryIdentity for value tests.

    :param kwargs: fields to override on the identity.
    :return: a TelemetryIdentity instance.
    """
    base = dict(host_name="h", node="n", server_address="s", bmc_ip="b")
    base.update(kwargs)
    return identity.TelemetryIdentity(**base)


def test_service_name_emitted_by_default():
    """Every series carries service.name=redfish_ctl without any flag."""
    dims = identity.build_identity_dimensions("172.25.230.29", vendor="supermicro")
    assert dims["service.name"] == "redfish_ctl"


def test_service_name_override():
    """--service-name overrides the default logical name on every series."""
    dims = identity.build_identity_dimensions(
        "172.25.230.29", vendor="supermicro", service_name="redfish-fleet")
    assert dims["service.name"] == "redfish-fleet"


def test_service_name_blank_falls_back_to_default():
    """A blank service.name resolves to the default."""
    assert _identity(service_name="   ").dimensions()["service.name"] == "redfish_ctl"


def test_service_name_rejects_too_long():
    """A service.name over 255 characters is rejected."""
    with pytest.raises(ValueError):
        _identity(service_name="x" * 256)


def test_service_name_rejects_secret_shape():
    """A credential-shaped service.name is rejected."""
    with pytest.raises(ValueError):
        _identity(service_name="ghp_deadbeefdeadbeef")


def test_service_name_is_reserved_for_extra_dimensions():
    """A caller may not set service.name via --dimension (it has a dedicated flag)."""
    with pytest.raises(ValueError, match="reserved"):
        identity.parse_dimension_pairs(["service.name=evil"])


def test_service_name_resolves_from_env(monkeypatch):
    """REDFISH_EXPORTER_SERVICE_NAME overrides the default in resolve_identity_options."""
    monkeypatch.setenv("REDFISH_EXPORTER_SERVICE_NAME", "from-env")
    assert identity.resolve_identity_options()["service_name"] == "from-env"


def test_service_name_is_resource_scoped_for_otlp():
    """service.name is a RESOURCE dimension, so the OTLP datapoint filter strips it."""
    assert "service.name" in identity.RESOURCE_DIMENSIONS
    assert "service.name" in otlp.RESOURCE_DIM_KEYS


def test_cli_normal_deploy_needs_no_service_name():
    """The documented normal deploy parses WITHOUT --service-name (it is override-only)."""
    parser, name, _ = Exporter.register_subcommand(Exporter)
    namespace = parser.parse_args([
        "--credential-file", "/etc/redfish/exporter.env",
        "--deployment-environment", "nv72-gb300",
        "--dimension", "telemetry.source=redfish",
        "--output", "signalfx", "--push-signalfx",
    ])
    assert name == "exporter"
    assert namespace.service_name is None  # unset -> resolves to redfish_ctl at runtime
    assert namespace.deployment_environment == "nv72-gb300"


def test_cli_service_name_override_parses():
    """--service-name parses when a caller needs a different logical name."""
    parser, _, _ = Exporter.register_subcommand(Exporter)
    namespace = parser.parse_args(["--service-name", "redfish-fleet"])
    assert namespace.service_name == "redfish-fleet"


def test_cli_full_service_identity_parses():
    """All OTel service identity fields have dedicated exporter flags."""
    parser, _, _ = Exporter.register_subcommand(Exporter)
    namespace = parser.parse_args([
        "--service-name", "redfish-fleet",
        "--service-namespace", "hardware",
        "--service-instance-id", "rack-a-exporter",
        "--service-version", "2.0.0",
        "--service-criticality", "critical",
    ])
    assert namespace.service_name == "redfish-fleet"
    assert namespace.service_namespace == "hardware"
    assert namespace.service_instance_id == "rack-a-exporter"
    assert namespace.service_version == "2.0.0"
    assert namespace.service_criticality == "critical"
