"""Single configuration loader - the ONLY module that reads the environment.

Application code must receive canonical configuration values from here; it must
never call ``os.getenv``, index ``os.environ``, or call :func:`env_first`
directly. Centralizing env access in one loader means one place defines every
setting, its canonical ``REDFISH_*`` name, and its default instead of the value
being re-derived at each call site.

The canonical setting model is specs/config/settings.yaml. :func:`env_first` is
the raw primitive the loader is built on; typed accessors
(``config.protocol.request_timeout`` and friends) are added here as call sites
migrate off direct env reads.

Enforced by tools/config_loader_gate.py (gate ``repo.config-loader``): a raw env
read anywhere outside this module fails the gate.

Author Mus spyroot@gmail.com
"""
from __future__ import annotations

import os
import re
import warnings
from dataclasses import dataclass
from typing import Optional

# A name whose value must never appear in an error message.
_SECRET_HINT = re.compile(r"PASSWORD|TOKEN|SECRET|KEY|CREDENTIAL", re.IGNORECASE)

# Boolean env-flag values treated as "on" by :func:`env_flag`.
_TRUTHY_FLAG_VALUES = frozenset({"1", "true", "yes", "on"})


class ConfigurationConflict(RuntimeError):
    """Two names for one setting are set to different values.

    Raised by :func:`env_first` when a canonical name and its deprecated alias
    (or any two names for the same setting) hold different values, so no silent
    override can pick a winner. See the registry specs/config/environment.yaml.
    """


@dataclass(frozen=True)
class EndpointConfig:
    """Resolved Redfish endpoint defaults from the process environment.

    :param host: BMC host or IP address.
    :param username: BMC account username.
    :param password: BMC account password.
    :param port: BMC TCP port.
    """

    host: str
    username: str
    password: str
    port: int


# Exporter identity environment names are defined here so telemetry callers do
# not read process environment state outside the canonical configuration loader.
_EXPORTER_IDENTITY_ENV_NAMES = {
    "host_prefix": (
        "REDFISH_EXPORTER_HOST_PREFIX",
        "IDRAC_EXPORTER_HOST_PREFIX",
    ),
    "bmc_octet_base": (
        "REDFISH_EXPORTER_BMC_OCTET_BASE",
        "IDRAC_EXPORTER_BMC_OCTET_BASE",
    ),
    "server_octet_base": (
        "REDFISH_EXPORTER_SERVER_OCTET_BASE",
        "IDRAC_EXPORTER_SERVER_OCTET_BASE",
    ),
    "server_subnet": (
        "REDFISH_EXPORTER_SERVER_SUBNET",
        "IDRAC_EXPORTER_SERVER_SUBNET",
    ),
    "deployment_environment": (
        "REDFISH_EXPORTER_DEPLOYMENT_ENVIRONMENT",
        "IDRAC_EXPORTER_DEPLOYMENT_ENVIRONMENT",
    ),
    "deployment_environment_compat": (
        "REDFISH_EXPORTER_DEPLOYMENT_ENVIRONMENT_COMPAT",
        "IDRAC_EXPORTER_DEPLOYMENT_ENVIRONMENT_COMPAT",
    ),
    "require_deployment_environment": (
        "REDFISH_EXPORTER_REQUIRE_DEPLOYMENT_ENVIRONMENT",
        "IDRAC_EXPORTER_REQUIRE_DEPLOYMENT_ENVIRONMENT",
    ),
    "extra_dimensions": (
        "REDFISH_EXPORTER_EXTRA_DIMENSIONS",
        "IDRAC_EXPORTER_EXTRA_DIMENSIONS",
    ),
    "service_name": (
        "REDFISH_EXPORTER_SERVICE_NAME",
        "IDRAC_EXPORTER_SERVICE_NAME",
    ),
    "service_namespace": (
        "REDFISH_EXPORTER_SERVICE_NAMESPACE",
        "IDRAC_EXPORTER_SERVICE_NAMESPACE",
    ),
    "service_instance_id": (
        "REDFISH_EXPORTER_SERVICE_INSTANCE_ID",
        "IDRAC_EXPORTER_SERVICE_INSTANCE_ID",
    ),
    "service_version": (
        "REDFISH_EXPORTER_SERVICE_VERSION",
        "IDRAC_EXPORTER_SERVICE_VERSION",
    ),
    "service_criticality": (
        "REDFISH_EXPORTER_SERVICE_CRITICALITY",
        "IDRAC_EXPORTER_SERVICE_CRITICALITY",
    ),
}


def _redacted(name: str, value: str) -> str:
    """Render ``name=value`` for an error, hiding secret values.

    :param name: the environment variable name.
    :param value: its value.
    :return: ``"NAME=value"``, or ``"NAME=<redacted>"`` for a secret-looking name.
    """
    return f"{name}=<redacted>" if _SECRET_HINT.search(name) else f"{name}={value}"


def env_first(
        *names: str, default: Optional[str] = None,
        strict: bool = True) -> Optional[str]:
    """Resolve one setting from its names, canonical first, conflict-aware.

    Legacy resolution, defined once (see specs/config/environment.yaml). Pass the
    canonical ``REDFISH_*`` name first, then any deprecated ``IDRAC_*`` alias:

    * only the canonical set -> its value;
    * only a legacy alias set -> its value, with a ``DeprecationWarning``;
    * both set to the same value -> the canonical value (legacy ignored);
    * both set to *different* values -> :class:`ConfigurationConflict` (no silent
      override); an empty value counts as set, so it too is a real value.

    :param names: variable names for one setting, canonical first.
    :param default: value returned when none of ``names`` is set.
    :param strict: when True, conflicting values raise
        :class:`ConfigurationConflict`; when False, the canonical-first value is
        returned so explicit CLI flags can still override parser defaults.
    :return: the resolved value, or ``default`` when none is set.
    :raises ConfigurationConflict: two names hold different values.
    """
    present = [(n, os.environ[n]) for n in names if n in os.environ]
    if not present:
        return default
    if len({v.strip() for _, v in present}) > 1:
        if not strict:
            return present[0][1]
        lines = "\n".join(f"  {_redacted(n, v)}" for n, v in present)
        raise ConfigurationConflict(
            f"Configuration conflict:\n{lines}\n\nUse only {names[0]}.")
    winner, value = present[0]
    if winner != names[0]:
        warnings.warn(
            f"{winner} is a deprecated alias for {names[0]}; set {names[0]} instead",
            DeprecationWarning, stacklevel=2)
    return value


def env_flag(name: str) -> bool:
    """Return whether a boolean env flag is set to a truthy value.

    Centralizes boolean env reads here (the config-loader gate forbids ``os.getenv``
    outside this loader). A flag counts as on when set to 1/true/yes/on.

    :param name: the environment variable to read.
    :return: True when the value is 1/true/yes/on (case-insensitive, trimmed), else False.
    """
    return os.getenv(name, "").strip().lower() in _TRUTHY_FLAG_VALUES


def env_float(name: str, default: float) -> float:
    """Return an env var parsed as a float, or a default when unset or non-numeric.

    :param name: the environment variable to read.
    :param default: the value returned when the variable is unset or not a valid float.
    :return: the parsed float, or ``default``.
    """
    try:
        return float(os.getenv(name, ""))
    except ValueError:
        return default


def http_timeout() -> float:
    """Return the HTTP request timeout for Redfish calls, in seconds.

    Resolves the canonical ``REDFISH_HTTP_TIMEOUT`` first, then the deprecated
    ``IDRAC_HTTP_TIMEOUT`` alias, via :func:`env_first`.

    :return: the request timeout in seconds (default 30).
    :raises ConfigurationConflict: when canonical and legacy values disagree.
    :raises ValueError: when the resolved value is not a valid float.
    """
    return float(env_first(
        "REDFISH_HTTP_TIMEOUT", "IDRAC_HTTP_TIMEOUT", default="30"))


def http_pool() -> int:
    """Return the HTTP connection pool size for the Redfish session.

    Resolves the canonical ``REDFISH_HTTP_POOL`` first, then the deprecated
    ``IDRAC_HTTP_POOL`` alias, via :func:`env_first`.

    :return: the connection pool size (default 4).
    :raises ConfigurationConflict: when canonical and legacy values disagree.
    :raises ValueError: when the resolved value is not a valid integer.
    """
    return int(env_first(
        "REDFISH_HTTP_POOL", "IDRAC_HTTP_POOL", default="4"))


def http_retries() -> int:
    """Return the HTTP retry total for the Redfish session.

    Resolves the canonical ``REDFISH_HTTP_RETRIES`` first, then the deprecated
    ``IDRAC_HTTP_RETRIES`` alias, via :func:`env_first`.

    :return: the total number of retries (default 3).
    :raises ConfigurationConflict: when canonical and legacy values disagree.
    :raises ValueError: when the resolved value is not a valid integer.
    """
    return int(env_first(
        "REDFISH_HTTP_RETRIES", "IDRAC_HTTP_RETRIES", default="3"))


def http_backoff() -> float:
    """Return the HTTP retry backoff factor for the Redfish session.

    Resolves the canonical ``REDFISH_HTTP_BACKOFF`` first, then the deprecated
    ``IDRAC_HTTP_BACKOFF`` alias, via :func:`env_first`.

    :return: the backoff factor in seconds (default 0.5).
    :raises ConfigurationConflict: when canonical and legacy values disagree.
    :raises ValueError: when the resolved value is not a valid float.
    """
    return float(env_first(
        "REDFISH_HTTP_BACKOFF", "IDRAC_HTTP_BACKOFF", default="0.5"))


def term_type() -> Optional[str]:
    """Return the terminal type from ``TERM``, or None when unset.

    A single OS-owned name with no canonical/legacy alias pair, so it reads
    ``os.environ`` directly rather than through :func:`env_first`. The caller
    branches on membership in a known-terminal list to decide colour output.

    :return: the raw ``TERM`` value, or None when the variable is not set.
    """
    return os.environ.get("TERM")


def otlp_protocol() -> Optional[str]:
    """Return the OTLP metrics protocol, metrics-signal overriding generic.

    Deliberately NOT :func:`env_first`: the OpenTelemetry spec allows
    ``OTEL_EXPORTER_OTLP_METRICS_PROTOCOL`` to differ from and override the
    generic ``OTEL_EXPORTER_OTLP_PROTOCOL`` (metrics wins), so a difference is
    valid, not a :class:`ConfigurationConflict`, and neither name is a
    deprecated alias. Plain metrics>generic precedence with a None default so
    the caller (or the OTLP SDK) supplies the ``grpc`` fallback.

    :return: the resolved protocol string, or None when neither name is set.
    """
    return (os.environ.get("OTEL_EXPORTER_OTLP_METRICS_PROTOCOL")
            or os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL")
            or None)


def exporter_config_file() -> Optional[str]:
    """Return the exporter config-file path from the environment, or None.

    Resolves the canonical ``REDFISH_EXPORTER_CONFIG_FILE`` first, then the
    deprecated ``IDRAC_EXPORTER_CONFIG_FILE`` alias, via :func:`env_first`; a
    mismatch is a hard :class:`ConfigurationConflict` (no silent override).

    :return: the configured file path, or None when neither name is set.
    :raises ConfigurationConflict: when canonical and legacy values disagree.
    """
    return env_first(
        "REDFISH_EXPORTER_CONFIG_FILE", "IDRAC_EXPORTER_CONFIG_FILE",
        default=None)


def exporter_credential_file() -> Optional[str]:
    """Return the exporter credential-file path from the environment, or None.

    Resolves the canonical ``REDFISH_EXPORTER_CREDENTIAL_FILE`` first, then the
    deprecated ``IDRAC_EXPORTER_CREDENTIAL_FILE`` alias, via :func:`env_first`.
    This is a path value, not a secret; a mismatch is a hard
    :class:`ConfigurationConflict`.

    :return: the configured credential file path, or None when neither is set.
    :raises ConfigurationConflict: when canonical and legacy values disagree.
    """
    return env_first(
        "REDFISH_EXPORTER_CREDENTIAL_FILE", "IDRAC_EXPORTER_CREDENTIAL_FILE",
        default=None)


def signalfx_ingest_url(explicit: Optional[str] = None) -> str:
    """Return the SignalFx ingest URL, an explicit value winning over env.

    A single owner-supplied name with no canonical/legacy alias, so it reads
    ``os.environ`` directly. Returns the raw value only; the not-set and
    ``/v2/datapoint`` validation stay in the caller's resolver.

    :param explicit: an explicitly supplied ingest URL that wins over the env.
    :return: the explicit URL, the ``SPLUNK_INGEST_URL`` value, or ``""``.
    """
    return explicit or os.environ.get("SPLUNK_INGEST_URL", "")


def signalfx_realm(explicit: Optional[str] = None) -> str:
    """Return the Splunk Observability realm, an explicit value winning over env.

    A single owner-supplied name with no canonical/legacy alias, so it reads
    ``os.environ`` directly. The caller interpolates the realm into the
    readback API host.

    :param explicit: an explicitly supplied realm that wins over the env.
    :return: the explicit realm, the ``SPLUNK_O11Y_REALM`` value, or ``""``.
    """
    return explicit or os.environ.get("SPLUNK_O11Y_REALM", "")


def signalfx_api_token(env_name: Optional[str] = None) -> str:
    """Return the Splunk API (read) token from a named env var.

    Secret: the returned value must never be logged. Only the env read lives
    here; the default env-name (``SPLUNK_API_TOKEN``) is owned by this loader.

    :param env_name: env var name holding the token; defaults to ``SPLUNK_API_TOKEN``.
    :return: the token value, or ``""`` when the named variable is unset.
    """
    return os.environ.get(env_name or "SPLUNK_API_TOKEN", "")


def signalfx_access_token(env_name: Optional[str] = None) -> str:
    """Return the Splunk ingest access token from a named env var.

    Secret: the returned value must never be logged. Only the env read lives
    here; the caller keeps its direct/file precedence and its ``name`` for the
    ``"{name} is not set"`` error message (which names the variable, never the
    value).

    :param env_name: env var name holding the token; defaults to ``SPLUNK_ACCESS_TOKEN``.
    :return: the token value, or ``""`` when the named variable is unset.
    """
    return os.environ.get(env_name or "SPLUNK_ACCESS_TOKEN", "")


def named_env(name: str, default: Optional[str] = None) -> Optional[str]:
    """Return the value of a runtime-named environment variable, or ``default``.

    The escape hatch for settings whose variable *name* is chosen at runtime by
    the caller (for example ``--keytab-base64-env NAME``, a Dell OEM secret env
    option, or a fleet node's credential-env field), so a fixed accessor cannot
    be pre-declared. The read still lives here so no call site touches
    ``os.environ`` directly. ``None`` is returned for an absent variable exactly
    as ``os.environ.get`` would, so a caller can distinguish absent (``None``)
    from empty (``""``). Values may be secret; never log them.

    :param name: the environment variable name, chosen by the caller.
    :param default: value returned when the variable is unset.
    :return: the variable's value, or ``default`` when it is not set.
    """
    return os.environ.get(name, default)


def discovery_retries() -> int:
    """Return the bounded retry count for discovery crawls.

    Resolves canonical ``REDFISH_DISCOVERY_RETRIES`` first, then the deprecated
    ``IDRAC_DISCOVERY_RETRIES`` alias, via :func:`env_first`.

    :return: the retry attempt count (default 4).
    :raises ConfigurationConflict: when canonical and legacy values disagree.
    :raises ValueError: when the resolved value is not a valid integer.
    """
    return int(env_first(
        "REDFISH_DISCOVERY_RETRIES", "IDRAC_DISCOVERY_RETRIES", default="4"))


def discovery_backoff() -> float:
    """Return the retry backoff factor for discovery crawls, in seconds.

    Resolves canonical ``REDFISH_DISCOVERY_BACKOFF`` first, then the deprecated
    ``IDRAC_DISCOVERY_BACKOFF`` alias, via :func:`env_first`.

    :return: the backoff factor in seconds (default 2.0).
    :raises ConfigurationConflict: when canonical and legacy values disagree.
    :raises ValueError: when the resolved value is not a valid float.
    """
    return float(env_first(
        "REDFISH_DISCOVERY_BACKOFF", "IDRAC_DISCOVERY_BACKOFF", default="2.0"))


def discovery_pace_ms() -> float:
    """Return the inter-request pacing for discovery crawls, in milliseconds.

    Resolves canonical ``REDFISH_DISCOVERY_PACE_MS`` first, then the deprecated
    ``IDRAC_DISCOVERY_PACE_MS`` alias, via :func:`env_first`. The caller converts
    the value to seconds.

    :return: the pacing delay in milliseconds (default 0).
    :raises ConfigurationConflict: when canonical and legacy values disagree.
    :raises ValueError: when the resolved value is not a valid float.
    """
    return float(env_first(
        "REDFISH_DISCOVERY_PACE_MS", "IDRAC_DISCOVERY_PACE_MS", default="0"))


def csdl_dir() -> Optional[str]:
    """Return the CSDL schema directory override from the environment, or None.

    A single owner-supplied name (``REDFISH_CSDL_DIR``) with no canonical/legacy
    alias, so it reads ``os.environ`` directly. The caller falls back to the
    bundled schema directory when this is unset or empty.

    :return: the configured CSDL directory, or None when the variable is unset.
    """
    return os.environ.get("REDFISH_CSDL_DIR")


def endpoint_defaults(strict: bool = True) -> EndpointConfig:
    """Return endpoint defaults from the canonical environment variables.

    :param strict: forwarded to :func:`env_first` for a consistent loader API.
    :return: endpoint defaults for the root CLI parser.
    :raises ValueError: when REDFISH_PORT is not an integer.
    """
    return EndpointConfig(
        host=env_first("REDFISH_IP", default="", strict=strict) or "",
        username=env_first(
            "REDFISH_USERNAME",
            default="root", strict=strict) or "",
        password=env_first(
            "REDFISH_PASSWORD",
            default="", strict=strict) or "",
        port=int(env_first(
            "REDFISH_PORT", default="443", strict=strict)),
    )


def exporter_build_revision(default: str = "unknown") -> str:
    """Return the exact source revision injected for exporter build telemetry.

    ``REDFISH_BUILD_REVISION`` is set by docker/Dockerfile's build argument or
    by the deployment environment.

    :param default: visible sentinel used when no revision was injected.
    :return: stripped revision value, or ``default`` when unset or blank.
    """
    value = env_first("REDFISH_BUILD_REVISION", default=default)
    return str(value or default).strip() or default


@dataclass(frozen=True)
class DmtfSimEndpoint:
    """Resolved endpoint for the persistent DMTF simulator.

    :param host: Kubernetes Service hostname or BMC IP address.
    :param port: simulator HTTP port.
    :param is_http: always True for the DMTF simulator contract.
    """

    host: str
    port: int
    is_http: bool = True


def required_dmtf_sim_endpoint() -> DmtfSimEndpoint:
    """Return the mandatory persistent DMTF simulator endpoint.

    The DMTF CI lane requires the canonical ``REDFISH_*`` names.

    :return: validated simulator host, port, and transport.
    :raises RuntimeError: when the endpoint is absent or malformed.
    """
    # Fail explicitly when the builder did not supply the canonical contract.
    if "REDFISH_IP" not in os.environ:
        raise RuntimeError(
            "REDFISH_IP is missing. The builder must provide the persistent "
            "DMTF simulator Service hostname."
        )

    if "REDFISH_PORT" not in os.environ:
        raise RuntimeError(
            "REDFISH_PORT is missing. The builder must provide the persistent "
            "DMTF simulator Service port."
        )

    host = (env_first("REDFISH_IP", default=None, strict=True) or "").strip()
    port_raw = (
        env_first("REDFISH_PORT", default=None, strict=True) or ""
    ).strip()

    if not host:
        raise RuntimeError("REDFISH_IP must not be empty")

    if host.startswith(("http://", "https://")):
        raise RuntimeError(
            "REDFISH_IP must contain only a hostname or IP address, "
            "not a URL scheme"
        )

    if "/" in host:
        raise RuntimeError("REDFISH_IP must not contain a URI path")

    try:
        port = int(port_raw)
    except ValueError as exc:
        raise RuntimeError("REDFISH_PORT must be an integer") from exc

    if not 1 <= port <= 65535:
        raise RuntimeError("REDFISH_PORT must be between 1 and 65535")

    return DmtfSimEndpoint(host=host, port=port, is_http=True)


def exporter_identity_env(
        overridden: tuple[str, ...] = ()) -> dict[str, Optional[str]]:
    """Return conflict-aware environment values for exporter identity.

    Each canonical ``REDFISH_EXPORTER_*`` setting and its deprecated
    ``IDRAC_EXPORTER_*`` alias are defined by ``specs/config/environment.yaml``.

    :param overridden: options explicitly supplied by CLI or config file.
    :return: identity option names mapped to their configured value or None.
    :raises ConfigurationConflict: when a canonical name and alias disagree.
    """
    return {
        option: env_first(*names, strict=option not in overridden)
        for option, names in _EXPORTER_IDENTITY_ENV_NAMES.items()
    }
