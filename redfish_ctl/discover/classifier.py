"""Pure vendor classification for a Redfish ServiceRoot.

A Redfish ``ServiceRoot`` (the document served at ``/redfish/v1/``) does not
carry a single canonical "vendor" field, so vendors leak their identity in a few
different places. This module reads those signals in a fixed priority order and
maps them to a small, stable set of vendor tags. It performs no I/O.

Ranking (first match wins):

1. ``Oem`` child key — e.g. ``ServiceRoot["Oem"]["Dell"]``. The OEM block is the
   strongest signal because a vendor only emits its own OEM namespace.
2. ``@odata.type`` OEM prefix — e.g. ``#DellServiceRoot.v1_0_0.DellServiceRoot``.
   Some services type their root with a vendor-prefixed schema name.
3. ``Manufacturer`` / ``Vendor`` substring — a free-text fallback, matched
   case-insensitively against known vendor names.

Anything unrecognized maps to ``"generic"`` so callers always get a usable tag.

Author Mus spyroot@gmail.com
"""
from typing import Any, Dict, Mapping, Optional

# Canonical vendor tags this tool emits.
DELL = "dell"
HPE = "hpe"
SUPERMICRO = "supermicro"
OPENBMC = "openbmc"
GENERIC = "generic"

# Map an OEM namespace / type token (lowercased) to a canonical vendor tag.
# Vendors use a couple of spellings (Dell/Emc, Hpe/Hp, Supermicro/SMC), so we
# normalize them all to one tag here. OpenBMC is firmware, not a hardware OEM, but
# it surfaces its own ``Oem.OpenBmc`` namespace so it is identifiable the same way.
_OEM_KEY_TO_VENDOR = {
    "dell": DELL,
    "emc": DELL,
    "hpe": HPE,
    "hp": HPE,
    "supermicro": SUPERMICRO,
    "smc": SUPERMICRO,
    "smci": SUPERMICRO,
    "openbmc": OPENBMC,
}

# Substrings searched in Manufacturer/Vendor free-text (lowercased). Ordered so
# the more specific token wins (e.g. "hpe" before a bare "hp").
_TEXT_TOKENS = (
    ("dell", DELL),
    ("hewlett packard enterprise", HPE),
    ("hpe", HPE),
    ("hewlett-packard", HPE),
    ("hewlett packard", HPE),
    ("hp", HPE),
    ("supermicro", SUPERMICRO),
    ("super micro", SUPERMICRO),
    ("openbmc", OPENBMC),
)


def _vendor_from_oem(service_root: Mapping[str, Any]) -> Optional[str]:
    """Return a vendor from the ``Oem`` block, or ``None``.

    The ``Oem`` value should be a mapping whose keys are vendor namespaces.
    A non-mapping ``Oem`` (malformed input) is ignored rather than raising.

    :param service_root: parsed ServiceRoot mapping to read the ``Oem`` block from.
    :return: a canonical vendor tag when an OEM namespace key is recognized, else
        ``None``.
    """
    oem = service_root.get("Oem")
    if not isinstance(oem, Mapping):
        return None
    for key in oem.keys():
        if not isinstance(key, str):
            continue
        vendor = _OEM_KEY_TO_VENDOR.get(key.strip().lower())
        if vendor is not None:
            return vendor
    return None


def _vendor_from_odata_type(service_root: Mapping[str, Any]) -> Optional[str]:
    """Return a vendor from an OEM-prefixed ``@odata.type``, or ``None``.

    Example: ``#DellServiceRoot.v1_0_0.DellServiceRoot`` -> ``dell``. The leading
    ``#`` and any namespace path are stripped before matching the prefix.

    :param service_root: parsed ServiceRoot mapping to read ``@odata.type`` from.
    :return: a canonical vendor tag when the schema name starts with a known OEM
        prefix, else ``None``.
    """
    odata_type = service_root.get("@odata.type")
    if not isinstance(odata_type, str):
        return None
    token = odata_type.lstrip("#")
    # Take the schema name portion before the first dot, e.g. "DellServiceRoot".
    schema = token.split(".", 1)[0].lower()
    for oem_key, vendor in _OEM_KEY_TO_VENDOR.items():
        if schema.startswith(oem_key):
            return vendor
    return None


def _vendor_from_text(service_root: Mapping[str, Any]) -> Optional[str]:
    """Return a vendor from ``Manufacturer``/``Vendor`` substrings, or ``None``.

    :param service_root: parsed ServiceRoot mapping to read ``Manufacturer`` and
        ``Vendor`` free-text fields from.
    :return: a canonical vendor tag when a known token is found in the combined
        free text, else ``None``.
    """
    parts = []
    for field in ("Manufacturer", "Vendor"):
        value = service_root.get(field)
        if isinstance(value, str):
            parts.append(value.lower())
    if not parts:
        return None
    haystack = " ".join(parts)
    for token, vendor in _TEXT_TOKENS:
        if token in haystack:
            return vendor
    return None


def classify_vendor(service_root: Optional[Dict[str, Any]]) -> str:
    """Classify the vendor of a Redfish ServiceRoot document.

    :param service_root: a parsed ``/redfish/v1/`` ServiceRoot dict. ``None`` or a
        non-mapping value is treated as unidentifiable.
    :return: one of ``"dell"``, ``"hpe"``, ``"supermicro"``, ``"openbmc"``, or
        ``"generic"``.

    The signals are consulted in a fixed ranking (Oem child key, then
    ``@odata.type`` OEM prefix, then ``Manufacturer``/``Vendor`` substring); the
    first that yields a known vendor wins. Unrecognized input is ``"generic"``,
    never an exception, so discovery never crashes on an odd root document.
    """
    if not isinstance(service_root, Mapping):
        return GENERIC

    for resolver in (_vendor_from_oem, _vendor_from_odata_type, _vendor_from_text):
        vendor = resolver(service_root)
        if vendor is not None:
            return vendor
    return GENERIC
