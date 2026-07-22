"""Single configuration loader - the ONLY module that reads the environment.

Application code must receive canonical configuration values from here; it must
never call ``os.getenv``, index ``os.environ``, or call :func:`env_first`
directly. Centralizing env access in one loader means one place defines every
setting, its canonical ``REDFISH_*`` name, its deprecated ``IDRAC_*`` alias, and
its default - instead of the value being re-derived at each call site.

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


_ENDPOINT_ENV_NAMES = {
    "host": ("REDFISH_IP", "IDRAC_IP"),
    "username": ("REDFISH_USERNAME", "IDRAC_USERNAME"),
    "password": ("REDFISH_PASSWORD", "IDRAC_PASSWORD"),
    "port": ("REDFISH_PORT", "IDRAC_PORT"),
}

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


def endpoint_conflict_fields() -> set[str]:
    """Return endpoint fields whose canonical and legacy env values disagree.

    :return: field names with conflicting endpoint environment values.
    """
    conflicts: set[str] = set()
    for field, names in _ENDPOINT_ENV_NAMES.items():
        try:
            env_first(*names)
        except ConfigurationConflict:
            conflicts.add(field)
    return conflicts


def endpoint_defaults(strict: bool = True) -> EndpointConfig:
    """Return endpoint defaults from canonical env vars and legacy aliases.

    The canonical REDFISH_* names are resolved first. Deprecated IDRAC_* names
    remain accepted as aliases through :func:`env_first`.

    :param strict: when True, conflicting env aliases raise; when False, the
        canonical-first value is returned for parser defaults so explicit CLI
        flags can still disambiguate.
    :return: endpoint defaults for the root CLI parser.
    :raises ConfigurationConflict: when canonical and legacy env vars disagree.
    :raises ValueError: when REDFISH_PORT/IDRAC_PORT is not an integer.
    """
    return EndpointConfig(
        host=env_first("REDFISH_IP", "IDRAC_IP", default="", strict=strict) or "",
        username=env_first(
            "REDFISH_USERNAME", "IDRAC_USERNAME",
            default="root", strict=strict) or "",
        password=env_first(
            "REDFISH_PASSWORD", "IDRAC_PASSWORD",
            default="", strict=strict) or "",
        port=int(env_first(
            "REDFISH_PORT", "IDRAC_PORT", default="443", strict=strict)),
    )


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
