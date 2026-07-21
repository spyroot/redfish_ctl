"""Deployment-environment identity + service.name/dimension flags for the exporter.

Covers issue #363: every exported series must carry the ``deployment.environment[.name]``
join dimension the observability dashboard filters on, the fail-closed
``--require-deployment-environment`` guard, validation of caller-supplied identity, and the
CLI accepting the documented invocation. The ``test_gate_*`` cases are the regression gate
that fails if the deployment dimension ever stops reaching the emitted series.

Author Mus spyroot@gmail.com
"""
import pytest

from redfish_ctl.telemetry import exporter, otlp
from redfish_ctl.telemetry.cmd_exporter import Exporter

_DEPLOY_ENVS = (
    "REDFISH_EXPORTER_DEPLOYMENT_ENVIRONMENT",
    "IDRAC_EXPORTER_DEPLOYMENT_ENVIRONMENT",
)
_SERVICE_ENVS = (
    "REDFISH_EXPORTER_SERVICE_NAME",
    "IDRAC_EXPORTER_SERVICE_NAME",
)


@pytest.fixture(autouse=True)
def _clear_identity_env(monkeypatch):
    """Keep deployment/service env vars from leaking into a test's resolution.

    :param monkeypatch: pytest environment patcher.
    """
    for name in _DEPLOY_ENVS + _SERVICE_ENVS:
        monkeypatch.delenv(name, raising=False)


def _samples_with_identity(deploy="nv72-gb300", extra=None):
    """Build samples through the REAL builder, exactly as ``collect_samples`` does.

    Seeds the identity the way ``collect_samples`` merges it, then builds samples via
    ``scrape_health_samples`` -> ``_with_dims`` (the production dimension choke point) so
    the gate would fail if that path ever drops the deployment/service/extra dimensions.

    :param deploy: deployment.environment value, or None for the 'unknown' default.
    :param extra: optional extra ``--dimension`` dict merged onto every sample.
    :return: list of MetricSample carrying the merged identity dimensions.
    """
    identity = exporter.build_identity_dimensions("172.25.230.41", vendor="supermicro")
    value = (exporter.resolve_deployment_environment(deploy) if deploy
             else exporter.resolve_deployment_environment())
    identity.update(exporter.deployment_dimensions(value))
    identity["service.name"] = exporter.resolve_service_name()
    if extra:
        identity.update(extra)
    return exporter.scrape_health_samples(identity, ok=True, duration_seconds=0.1)


# --- resolve_deployment_environment -------------------------------------------------

def test_deployment_environment_defaults_to_unknown():
    """With nothing set, the value defaults to the 'unknown' sentinel."""
    assert exporter.resolve_deployment_environment() == "unknown"


def test_deployment_environment_explicit_is_lowercased():
    """An explicit value is returned lowercased (matches the co-resident dimension)."""
    assert exporter.resolve_deployment_environment("NV72-GB300") == "nv72-gb300"


def test_deployment_environment_explicit_beats_env(monkeypatch):
    """An explicit CLI value takes precedence over the env var."""
    monkeypatch.setenv("REDFISH_EXPORTER_DEPLOYMENT_ENVIRONMENT", "from-env")
    assert exporter.resolve_deployment_environment("from-cli") == "from-cli"


def test_deployment_environment_env_used_when_no_explicit(monkeypatch):
    """The primary env var is used when no explicit value is given."""
    monkeypatch.setenv("REDFISH_EXPORTER_DEPLOYMENT_ENVIRONMENT", "nv72-gb300")
    assert exporter.resolve_deployment_environment() == "nv72-gb300"


def test_deployment_environment_legacy_env_fallback(monkeypatch):
    """The legacy IDRAC_ env var is honored as a fallback."""
    monkeypatch.setenv("IDRAC_EXPORTER_DEPLOYMENT_ENVIRONMENT", "legacy-env")
    assert exporter.resolve_deployment_environment() == "legacy-env"


def test_deployment_environment_config_value_used():
    """A JSON-config value is used when no explicit/env value is present."""
    assert exporter.resolve_deployment_environment(config_value="cfg-env") == "cfg-env"


@pytest.mark.parametrize("bad", ["unknown", "None", "null", "n/a"])
def test_deployment_environment_rejects_literal_sentinel(bad):
    """An explicit literal-sentinel value is a hard error, not a silent default."""
    with pytest.raises(ValueError):
        exporter.resolve_deployment_environment(bad)


@pytest.mark.parametrize("blank", ["", "   "])
def test_deployment_environment_blank_falls_back_to_default(blank):
    """An explicit empty/whitespace value is treated as unset -> the 'unknown' default.

    (--require-deployment-environment then catches 'unknown' fail-closed in a fleet.)
    """
    assert exporter.resolve_deployment_environment(blank) == "unknown"


@pytest.mark.parametrize("bad", ["Bad Env", "env/slash", "a" * 64, "-lead", "trail-"])
def test_deployment_environment_rejects_invalid_charset(bad):
    """Values outside the DNS-ish label grammar are rejected."""
    with pytest.raises(ValueError):
        exporter.resolve_deployment_environment(bad)


def test_deployment_environment_rejects_credential_shape():
    """A credential-shaped value is refused as telemetry identity."""
    with pytest.raises(ValueError):
        exporter.resolve_deployment_environment("user@host")


def test_deployment_environment_rejects_credential_before_lowercasing():
    """An AWS-key-shaped value is rejected (the guard runs before lowercasing)."""
    with pytest.raises(ValueError):
        exporter.resolve_deployment_environment("AKIAIOSFODNN7EXAMPLE")


def test_deployment_environment_error_does_not_echo_value():
    """A rejected value is never echoed in the error message (avoids logging a secret)."""
    secret = "s3cr3t-not-a-valid-env!"
    with pytest.raises(ValueError) as exc:
        exporter.resolve_deployment_environment(secret)
    assert secret not in str(exc.value)


def test_require_deployment_environment_fails_closed():
    """--require with nothing set fails closed instead of defaulting to 'unknown'."""
    with pytest.raises(ValueError):
        exporter.resolve_deployment_environment(require=True)


def test_require_deployment_environment_passes_with_value(monkeypatch):
    """--require succeeds when a real value is provided by the env."""
    monkeypatch.setenv("REDFISH_EXPORTER_DEPLOYMENT_ENVIRONMENT", "nv72-gb300")
    assert exporter.resolve_deployment_environment(require=True) == "nv72-gb300"


# --- deployment_dimensions (dual-emit + compat) -------------------------------------

def test_deployment_dimensions_dual_emits_both_by_default():
    """The default compat mode emits both keys with the same value."""
    assert exporter.deployment_dimensions("nv72-gb300") == {
        "deployment.environment": "nv72-gb300",
        "deployment.environment.name": "nv72-gb300",
    }


def test_deployment_dimensions_deprecated_only():
    """compat=deprecated emits only the deprecated key."""
    assert exporter.deployment_dimensions("x", compat="deprecated") == {
        "deployment.environment": "x"}


def test_deployment_dimensions_stable_only():
    """compat=stable emits only the stable key."""
    assert exporter.deployment_dimensions("x", compat="stable") == {
        "deployment.environment.name": "x"}


def test_deployment_dimensions_bad_compat():
    """An unknown compat mode is rejected."""
    with pytest.raises(ValueError):
        exporter.deployment_dimensions("x", compat="nope")


# --- parse_extra_dimensions ---------------------------------------------------------

def test_parse_extra_dimensions_valid():
    """A valid key=value dimension is parsed and whitespace-stripped."""
    assert exporter.parse_extra_dimensions([" telemetry.source = redfish "]) == {
        "telemetry.source": "redfish"}


def test_parse_extra_dimensions_none():
    """None yields an empty dict."""
    assert exporter.parse_extra_dimensions(None) == {}


@pytest.mark.parametrize("bad", ["novalue", "=v", "k=", "k= "])
def test_parse_extra_dimensions_rejects_malformed(bad):
    """A non key=value or empty-side entry is rejected."""
    with pytest.raises(ValueError):
        exporter.parse_extra_dimensions([bad])


@pytest.mark.parametrize(
    "key", ["vendor", "host.name", "deployment.environment", "service.name"])
def test_parse_extra_dimensions_rejects_reserved(key):
    """A caller may not override a discovered identity key, the deployment keys, or service.name."""
    with pytest.raises(ValueError):
        exporter.parse_extra_dimensions([f"{key}=x"])


def test_parse_extra_dimensions_rejects_credential_value():
    """A credential-shaped dimension value is refused."""
    with pytest.raises(ValueError):
        exporter.parse_extra_dimensions(["token=ghp_deadbeefdeadbeef"])


# --- resolve_service_name -----------------------------------------------------------

def test_service_name_default():
    """service.name defaults to redfish_ctl."""
    assert exporter.resolve_service_name() == "redfish_ctl"


def test_service_name_explicit_preserved():
    """An explicit service.name is returned as-is (case preserved)."""
    assert exporter.resolve_service_name("My-Exporter") == "My-Exporter"


def test_service_name_blank_falls_back_to_default():
    """An explicit empty/whitespace service.name is treated as unset -> the default."""
    assert exporter.resolve_service_name("   ") == "redfish_ctl"


# --- GATE: every emitted series carries deployment.environment[.name] (issue #363) ---

def test_gate_with_dims_preserves_full_identity():
    """GATE: _with_dims (the production dimension choke point) preserves the full identity.

    Regression guard for #363: an earlier version rebuilt each sample's dimensions from the
    REQUIRED_DIMENSIONS allowlist only, silently dropping deployment.environment (and the
    --dimension extras) before they reached any emitted series.
    """
    identity = exporter.build_identity_dimensions("172.25.230.41", vendor="supermicro")
    identity.update(exporter.deployment_dimensions("nv72-gb300"))
    identity["service.name"] = "redfish_ctl"
    identity["telemetry.source"] = "redfish"
    dims = exporter._with_dims(identity, source="environment", sensor="s1")
    assert dims["deployment.environment"] == "nv72-gb300"
    assert dims["deployment.environment.name"] == "nv72-gb300"
    assert dims["service.name"] == "redfish_ctl"
    assert dims["telemetry.source"] == "redfish"
    assert dims["source"] == "environment"
    for key in exporter.REQUIRED_DIMENSIONS:
        assert key in dims


def test_gate_signalfx_series_carry_deployment_environment():
    """GATE: every SignalFx series carries both deployment keys (regression guard for #363)."""
    body = exporter.to_signalfx_body(_samples_with_identity())
    assert body["gauge"], "expected at least one series"
    for series in body["gauge"]:
        dims = series["dimensions"]
        assert dims.get("deployment.environment") == "nv72-gb300"
        assert dims.get("deployment.environment.name") == "nv72-gb300"


def test_gate_signalfx_series_carry_default_service_name():
    """GATE: every SignalFx series carries service.name=redfish_ctl by default (no flag)."""
    body = exporter.to_signalfx_body(_samples_with_identity())
    assert body["gauge"], "expected at least one series"
    for series in body["gauge"]:
        assert series["dimensions"].get("service.name") == "redfish_ctl"


def test_gate_prometheus_series_carry_deployment_environment():
    """GATE: every Prometheus series line carries both deployment labels."""
    text = exporter.render_prometheus_text(_samples_with_identity())
    metric_lines = [ln for ln in text.splitlines() if ln and not ln.startswith("# ")]
    assert metric_lines
    for line in metric_lines:
        assert 'deployment.environment="nv72-gb300"' in line
        assert 'deployment.environment.name="nv72-gb300"' in line


def test_gate_extra_dimension_reaches_signalfx():
    """A --dimension extra (telemetry.source) reaches the SignalFx plane."""
    body = exporter.to_signalfx_body(
        _samples_with_identity(extra={"telemetry.source": "redfish"}))
    for series in body["gauge"]:
        assert series["dimensions"].get("telemetry.source") == "redfish"


# --- OTLP resource: deployment.* is resource-scoped, omitted when 'unknown' ----------

def test_otlp_deployment_keys_are_resource_scoped():
    """deployment.* are in RESOURCE_DIM_KEYS, so the datapoint filter strips them."""
    assert "deployment.environment" in otlp.RESOURCE_DIM_KEYS
    assert "deployment.environment.name" in otlp.RESOURCE_DIM_KEYS


def test_otlp_resource_lifts_real_deployment_environment():
    """A real deployment.environment rides the OTLP Resource."""
    attrs = otlp._resource_attrs(_samples_with_identity("nv72-gb300"), "redfish_ctl")
    assert attrs["deployment.environment"] == "nv72-gb300"
    assert attrs["deployment.environment.name"] == "nv72-gb300"


def test_otlp_resource_omits_unknown_deployment_environment():
    """An 'unknown' deployment.environment is omitted from the OTLP Resource (OTel idiom)."""
    attrs = otlp._resource_attrs(_samples_with_identity(deploy=None), "redfish_ctl")
    assert "deployment.environment" not in attrs
    assert "deployment.environment.name" not in attrs
    assert attrs["service.name"] == "redfish_ctl"


# --- CLI acceptance: the documented invocation parses -------------------------------

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
    assert namespace.deployment_environment == "nv72-gb300"
    assert namespace.deployment_environment_compat == "both"
    assert namespace.require_deployment_environment is False
    assert namespace.service_name is None  # unset -> resolves to redfish_ctl at runtime
    assert namespace.dimension == ["telemetry.source=redfish"]
    assert namespace.exporter_output == "signalfx"
    assert namespace.push_signalfx is True


def test_cli_service_name_override_parses():
    """--service-name overrides the default logical name when a caller needs a different one."""
    parser, _, _ = Exporter.register_subcommand(Exporter)
    namespace = parser.parse_args(["--service-name", "redfish-fleet"])
    assert namespace.service_name == "redfish-fleet"


def test_cli_accepts_require_and_compat_flags():
    """--require-deployment-environment and --deployment-environment-compat parse."""
    parser, _, _ = Exporter.register_subcommand(Exporter)
    namespace = parser.parse_args([
        "--deployment-environment", "nv72-gb300",
        "--require-deployment-environment",
        "--deployment-environment-compat", "stable",
    ])
    assert namespace.require_deployment_environment is True
    assert namespace.deployment_environment_compat == "stable"
