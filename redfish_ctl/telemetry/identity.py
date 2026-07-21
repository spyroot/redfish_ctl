"""Telemetry identity and fixed-dimension helpers."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional

DimensionSource = Optional[Mapping | Iterable[str] | str]

IDENTITY_DIMENSIONS = ("host.name", "node", "server.address", "bmc.ip", "vendor")
DEPLOYMENT_ENVIRONMENT_DIM = "deployment.environment"
DEPLOYMENT_ENVIRONMENT_NAME_DIM = "deployment.environment.name"
DEPLOYMENT_DIMENSIONS = (DEPLOYMENT_ENVIRONMENT_DIM, DEPLOYMENT_ENVIRONMENT_NAME_DIM)
RESOURCE_DIMENSIONS = IDENTITY_DIMENSIONS + DEPLOYMENT_DIMENSIONS

_DIMENSION_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,127}$")
_DEPLOYMENT_ENV_RE = re.compile(r"^[a-z0-9._-]{1,63}$")
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_SECRET_PREFIXES = ("ghp_", "gho_", "xox", "akia", "bearer")
_MISSING_DEPLOYMENT_SENTINELS = {"unknown", "none", "null", "n-a", "na"}
_COMPAT_MODES = {"both", "deprecated", "stable"}
_RESERVED_EXTRA_DIMENSIONS = set(IDENTITY_DIMENSIONS) | set(DEPLOYMENT_DIMENSIONS) | {
    "deployment",
    "model",
}
_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}

_IDENTITY_ENV_KEYS = {
    "host_prefix": ("REDFISH_EXPORTER_HOST_PREFIX", "IDRAC_EXPORTER_HOST_PREFIX"),
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
}


@dataclass(frozen=True)
class TelemetryIdentity:
    """Fixed identity carried by every telemetry sample from one exporter."""

    host_name: str
    node: str
    server_address: str
    bmc_ip: str
    vendor: str = "unknown"
    deployment_environment: Optional[str] = None
    deployment_environment_compat: str = "both"
    require_deployment_environment: bool = False
    extra_dimensions: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalize and validate caller-controlled identity fields."""
        deployment_environment = _normalize_deployment_environment(
            self.deployment_environment,
            required=self.require_deployment_environment,
        )
        compat = str(self.deployment_environment_compat or "both").strip().lower()
        if compat not in _COMPAT_MODES:
            raise ValueError(
                "deployment_environment_compat must be one of "
                "both, deprecated, or stable")
        object.__setattr__(self, "deployment_environment", deployment_environment)
        object.__setattr__(self, "deployment_environment_compat", compat)
        object.__setattr__(self, "vendor", str(self.vendor or "unknown").lower())
        object.__setattr__(
            self,
            "extra_dimensions",
            parse_dimension_pairs(self.extra_dimensions),
        )

    def dimensions(self) -> dict[str, str]:
        """Return fixed dimensions projected onto every metric sample.

        :return: identity and configured fixed dimensions for one exporter.
        """
        dims = {
            "host.name": str(self.host_name),
            "node": str(self.node),
            "server.address": str(self.server_address),
            "bmc.ip": str(self.bmc_ip),
            "vendor": str(self.vendor),
        }
        if self.deployment_environment:
            if self.deployment_environment_compat in {"both", "deprecated"}:
                dims[DEPLOYMENT_ENVIRONMENT_DIM] = self.deployment_environment
            if self.deployment_environment_compat in {"both", "stable"}:
                dims[DEPLOYMENT_ENVIRONMENT_NAME_DIM] = self.deployment_environment
        dims.update(self.extra_dimensions)
        return dims

    def resource_attributes(self) -> dict[str, str]:
        """Return the identity fields that should become OTLP resource attributes.

        :return: resource attribute dimensions present in this identity.
        """
        dims = self.dimensions()
        return {key: dims[key] for key in RESOURCE_DIMENSIONS if key in dims}


def build_legacy_gb300_identity(
        bmc_ip: str,
        vendor: str = "unknown",
        host_prefix: str = "gb300-poc1",
        bmc_octet_base: int = 20,
        server_octet_base: int = 40,
        server_subnet: Optional[str] = None,
        deployment_environment: Optional[str] = None,
        deployment_environment_compat: str = "both",
        require_deployment_environment: bool = False,
        extra_dimensions: DimensionSource = None) -> TelemetryIdentity:
    """Build the compatibility identity strategy used by existing GB300 deploys.

    :param bmc_ip: BMC address or label used as the telemetry source identity.
    :param vendor: hardware vendor label for the ``vendor`` dimension.
    :param host_prefix: prefix used when deriving ``host.name`` from a GB300 slot.
    :param bmc_octet_base: BMC last-octet offset subtracted to compute slot number.
    :param server_octet_base: server last-octet offset added to the slot number.
    :param server_subnet: optional three-octet subnet for ``server.address``.
    :param deployment_environment: optional deployment join label value.
    :param deployment_environment_compat: deployment label compatibility mode.
    :param require_deployment_environment: whether missing deployment labels fail.
    :param extra_dimensions: additional fixed ``KEY=VALUE`` dimensions to validate.
    :return: validated telemetry identity for a single exporter instance.
    """
    bmc = _validate_bmc_label(bmc_ip)
    parts = bmc.split(".")
    bmc_base = _coerce_int(bmc_octet_base, "bmc_octet_base")
    server_base = _coerce_int(server_octet_base, "server_octet_base")
    if len(parts) == 4 and all(part.isdigit() for part in parts):
        octets = [_coerce_octet(part, "bmc_ip") for part in parts]
        slot = octets[-1] - bmc_base
        if slot < 0:
            raise ValueError("derived slot must be non-negative")
        subnet = server_subnet or ".".join(str(part) for part in octets[:3])
        _validate_ipv4_subnet(subnet)
        node = f"slot{slot}"
        host = f"{host_prefix}-{node}"
        server = f"{subnet}.{server_base + slot}"
        _validate_ipv4_address(server)
    else:
        node = "unknown"
        host = bmc
        server = "unknown"
    return TelemetryIdentity(
        host_name=host,
        node=node,
        server_address=server,
        bmc_ip=bmc,
        vendor=vendor,
        deployment_environment=deployment_environment,
        deployment_environment_compat=deployment_environment_compat,
        require_deployment_environment=require_deployment_environment,
        extra_dimensions=parse_dimension_pairs(extra_dimensions),
    )


def build_identity_dimensions(
        bmc_ip: str,
        vendor: str = "unknown",
        host_prefix: str = "gb300-poc1",
        bmc_octet_base: int = 20,
        server_octet_base: int = 40,
        server_subnet: Optional[str] = None,
        deployment_environment: Optional[str] = None,
        deployment_environment_compat: str = "both",
        require_deployment_environment: bool = False,
        extra_dimensions: DimensionSource = None) -> dict[str, str]:
    """Return the fixed join dimensions required on every exported series.

    :param bmc_ip: BMC address or label used as the telemetry source identity.
    :param vendor: hardware vendor label for the ``vendor`` dimension.
    :param host_prefix: prefix used when deriving ``host.name`` from a GB300 slot.
    :param bmc_octet_base: BMC last-octet offset subtracted to compute slot number.
    :param server_octet_base: server last-octet offset added to the slot number.
    :param server_subnet: optional three-octet subnet for ``server.address``.
    :param deployment_environment: optional deployment join label value.
    :param deployment_environment_compat: deployment label compatibility mode.
    :param require_deployment_environment: whether missing deployment labels fail.
    :param extra_dimensions: additional fixed ``KEY=VALUE`` dimensions to validate.
    :return: dimension mapping applied to every exported metric sample.
    """
    return build_legacy_gb300_identity(
        bmc_ip=bmc_ip,
        vendor=vendor,
        host_prefix=host_prefix,
        bmc_octet_base=bmc_octet_base,
        server_octet_base=server_octet_base,
        server_subnet=server_subnet,
        deployment_environment=deployment_environment,
        deployment_environment_compat=deployment_environment_compat,
        require_deployment_environment=require_deployment_environment,
        extra_dimensions=extra_dimensions,
    ).dimensions()


def resolve_identity_options(
        host_prefix: Optional[str] = None,
        bmc_octet_base: Optional[int] = None,
        server_octet_base: Optional[int] = None,
        server_subnet: Optional[str] = None,
        deployment_environment: Optional[str] = None,
        deployment_environment_compat: Optional[str] = None,
        require_deployment_environment: Optional[bool] = None,
        extra_dimensions: DimensionSource = None) -> dict:
    """Resolve exporter identity options from args/config, env, and defaults.

    :param host_prefix: explicit host-name prefix override.
    :param bmc_octet_base: explicit BMC octet base override.
    :param server_octet_base: explicit server octet base override.
    :param server_subnet: explicit server subnet override.
    :param deployment_environment: explicit deployment join label value.
    :param deployment_environment_compat: explicit deployment label mode.
    :param require_deployment_environment: explicit missing-label failure policy.
    :param extra_dimensions: explicit additional fixed dimensions.
    :return: keyword arguments for :func:`build_identity_dimensions`.
    """
    resolved_host_prefix = _first_non_empty(
        host_prefix,
        *(os.environ.get(name) for name in _IDENTITY_ENV_KEYS["host_prefix"]),
        "gb300-poc1",
    )
    resolved_bmc_octet_base = _first_non_empty(
        bmc_octet_base,
        *(os.environ.get(name) for name in _IDENTITY_ENV_KEYS["bmc_octet_base"]),
        20,
    )
    resolved_server_octet_base = _first_non_empty(
        server_octet_base,
        *(os.environ.get(name) for name in _IDENTITY_ENV_KEYS["server_octet_base"]),
        40,
    )
    resolved_server_subnet = _first_non_empty(
        server_subnet,
        *(os.environ.get(name) for name in _IDENTITY_ENV_KEYS["server_subnet"]),
    )
    resolved_deployment_environment = _first_non_empty(
        deployment_environment,
        *(os.environ.get(name) for name in _IDENTITY_ENV_KEYS["deployment_environment"]),
    )
    resolved_deployment_environment_compat = _first_non_empty(
        deployment_environment_compat,
        *(
            os.environ.get(name)
            for name in _IDENTITY_ENV_KEYS["deployment_environment_compat"]
        ),
        "both",
    )
    resolved_require_deployment_environment = _first_non_empty(
        require_deployment_environment,
        *(
            os.environ.get(name)
            for name in _IDENTITY_ENV_KEYS["require_deployment_environment"]
        ),
        False,
    )
    resolved_extra_dimensions = _first_non_empty(
        extra_dimensions,
        *(os.environ.get(name) for name in _IDENTITY_ENV_KEYS["extra_dimensions"]),
    )
    return {
        "host_prefix": str(resolved_host_prefix),
        "bmc_octet_base": _coerce_int(resolved_bmc_octet_base, "bmc_octet_base"),
        "server_octet_base": _coerce_int(
            resolved_server_octet_base, "server_octet_base"),
        "server_subnet": (
            str(resolved_server_subnet)
            if resolved_server_subnet is not None
            else None
        ),
        "deployment_environment": resolved_deployment_environment,
        "deployment_environment_compat": str(resolved_deployment_environment_compat),
        "require_deployment_environment": _coerce_bool(
            resolved_require_deployment_environment,
            "require_deployment_environment",
        ),
        "extra_dimensions": parse_dimension_pairs(resolved_extra_dimensions),
    }


def parse_dimension_pairs(value: DimensionSource) -> dict[str, str]:
    """Parse fixed exporter dimensions from a mapping, CSV string, or pairs.

    :param value: mapping, comma-separated string, or iterable of ``KEY=VALUE``.
    :return: validated fixed dimension mapping.
    """
    if value in (None, ""):
        return {}
    if isinstance(value, Mapping):
        raw_items = list(value.items())
    else:
        parts = value.split(",") if isinstance(value, str) else list(value)
        raw_items = []
        for part in parts:
            if part in (None, ""):
                continue
            text = str(part).strip()
            if not text:
                continue
            if "=" not in text:
                raise ValueError(f"dimension {text!r} must use KEY=VALUE")
            key, item_value = text.split("=", 1)
            raw_items.append((key, item_value))

    dimensions: dict[str, str] = {}
    for raw_key, raw_value in raw_items:
        key = str(raw_key).strip()
        dim_value = str(raw_value).strip()
        _validate_extra_dimension(key, dim_value)
        dimensions[key] = dim_value
    return dimensions


def common_sample_dimensions(samples: Iterable) -> dict[str, str]:
    """Return dimensions that have the same value on every sample.

    :param samples: metric samples to compare for common fixed dimensions.
    :return: dimensions present with the same value on every sample.
    """
    iterator = iter(samples)
    try:
        first = next(iterator)
    except StopIteration:
        return {}
    common = dict(first.dimensions)
    for sample in iterator:
        for key in list(common):
            if sample.dimensions.get(key) != common[key]:
                common.pop(key, None)
    return common


def _normalize_deployment_environment(value, required: bool = False) -> Optional[str]:
    """Normalize a caller-supplied deployment environment.

    :param value: raw deployment environment value.
    :param required: whether a missing value should raise ``ValueError``.
    :return: normalized deployment environment or None when optional and absent.
    """
    if value in (None, ""):
        if required:
            raise ValueError("deployment environment is required")
        return None
    text = str(value).strip().lower()
    if not text or text in _MISSING_DEPLOYMENT_SENTINELS:
        raise ValueError("deployment environment must be a concrete value")
    if _looks_secret(text):
        raise ValueError("deployment environment must not contain secret material")
    if not _DEPLOYMENT_ENV_RE.fullmatch(text):
        raise ValueError(
            "deployment environment must use 1-63 chars from [a-z0-9._-]")
    return text


def _validate_extra_dimension(key: str, value: str) -> None:
    """Validate one caller-provided fixed dimension.

    :param key: proposed fixed dimension key.
    :param value: proposed fixed dimension value.
    """
    if not key or not value:
        raise ValueError("dimension must have a non-empty key and value")
    if not _DIMENSION_KEY_RE.fullmatch(key):
        raise ValueError(f"dimension key {key!r} is not valid")
    if key in _RESERVED_EXTRA_DIMENSIONS:
        raise ValueError(
            f"dimension {key!r} is reserved; use the dedicated identity option")
    if _looks_secret(key) or _looks_secret(value):
        raise ValueError(f"dimension {key!r} must not contain secret material")


def _looks_secret(value: str) -> bool:
    """Return True for deterministic secret-like shapes.

    :param value: value to inspect.
    :return: True when the value looks like credential material.
    """
    text = str(value)
    lowered = text.lower()
    return (
        "://" in text
        or "@" in text
        or any(prefix in lowered for prefix in _SECRET_PREFIXES)
        or bool(_JWT_RE.search(text))
    )


def _validate_bmc_label(value: str) -> str:
    """Validate a BMC label before it becomes a metric dimension.

    :param value: raw BMC address or label.
    :return: safe non-empty BMC label.
    """
    text = str(value or "unknown").strip()
    if _looks_secret(text):
        raise ValueError("BMC identity must not contain credentials or URLs")
    return text or "unknown"


def _validate_ipv4_subnet(value: str) -> None:
    """Validate a dotted three-octet IPv4 subnet.

    :param value: raw subnet value.
    """
    parts = str(value).split(".")
    if len(parts) != 3:
        raise ValueError(f"server_subnet must have three IPv4 octets; got {value!r}")
    for part in parts:
        _coerce_octet(part, "server_subnet")


def _validate_ipv4_address(value: str) -> None:
    """Validate a dotted four-octet IPv4 address.

    :param value: raw address value.
    """
    parts = str(value).split(".")
    if len(parts) != 4:
        raise ValueError(f"server address must have four IPv4 octets; got {value!r}")
    for part in parts:
        _coerce_octet(part, "server address")


def _coerce_octet(value: str, field_name: str) -> int:
    """Coerce and range-check one IPv4 octet.

    :param value: raw octet value.
    :param field_name: field name included in validation errors.
    :return: integer octet in the inclusive range 0..255.
    """
    try:
        octet = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{field_name} octet must be an integer; got {value!r}"
        ) from exc
    if not 0 <= octet <= 255:
        raise ValueError(f"{field_name} octet must be in range 0..255; got {octet}")
    return octet


def _coerce_int(value, field_name: str) -> int:
    """Coerce an integer config field with a targeted error message.

    :param value: raw integer-like value.
    :param field_name: field name included in validation errors.
    :return: coerced integer value.
    """
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer; got {value!r}") from exc


def _coerce_bool(value, field_name: str) -> bool:
    """Coerce a bool-like config field.

    :param value: raw bool-like value.
    :param field_name: field name included in validation errors.
    :return: coerced boolean value.
    """
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUTHY:
        return True
    if text in _FALSY:
        return False
    raise ValueError(f"{field_name} must be a boolean; got {value!r}")


def _non_empty(value):
    """Return ``value`` with blank strings collapsed to None.

    :param value: raw value to normalize.
    :return: original value, stripped string, or None for blank strings.
    """
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value


def _first_non_empty(*values):
    """Return the first non-empty value from ``values``.

    :param values: candidate values in precedence order.
    :return: first value that is not None or blank, else None.
    """
    for value in values:
        cleaned = _non_empty(value)
        if cleaned is not None:
            return cleaned
    return None
