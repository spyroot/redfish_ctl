"""Vendor profiles: per-connection strategy objects owning the vendor chokepoints.

A VendorProfile binds vendor semantics at the axis vendor actually varies on —
the CONNECTION — instead of class ancestry. The profile owns ONLY the
chokepoints (status decode, task-id parsing, task/job polling); the neutral
manager delegates to it, and the ~200 registered command classes never change.
:class:`DmtfProfile` is the shared base (the DMTF lower denominator); a vendor
profile overrides only what that vendor genuinely diverges on. Profiles are
stateless singletons resolved once per connection from the ServiceRoot via the
existing ``discover.classifier.classify_vendor`` ladder and cached by
connection fingerprint.

Design record: ``.internal/design-notes/2026-07-24-vendor-dispatch-panel.json``
(chokepoint-strategy, both judges concur). The chokepoint method BODIES are
relocated from the managers by the implementation pass; each seam below names
its exact source anchor. Plumbing-complete today: registration (loud collision),
resolution (observable generic fallback), ServiceRoot detection, and the
connection cache.

Author Mus spyroot@gmail.com
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Dict, Optional, Tuple, Type

from .discover.classifier import classify_vendor
from .redfish_exceptions import ProfileRegistrationCollision

logger = logging.getLogger(__name__)

# vendor key -> profile class. Written only by register_profile().
_PROFILE_REGISTRY: Dict[str, Type["DmtfProfile"]] = {}

# Observable fallback: how many times an unknown vendor fell back to generic,
# keyed by the unknown vendor string. A silent wrong-profile resolution is the
# design's worst failure mode; this counter plus one log line makes it loud.
FALLBACK_COUNTS: Dict[str, int] = {}

# (host, port) -> resolved profile instance; one probe per connection.
_PROFILE_CACHE: Dict[Tuple[str, int], "DmtfProfile"] = {}
_CACHE_LOCK = threading.Lock()


def register_profile(vendor: str, profile_cls: Type["DmtfProfile"]) -> None:
    """Register a profile class for a vendor key.

    :param vendor: lowercase vendor key (``dell``, ``supermicro``, ...).
    :param profile_cls: the profile class serving that vendor.
    :raises ProfileRegistrationCollision: when the key is already registered
        to a DIFFERENT class — import-time and loud, never last-write-wins.
    """
    existing = _PROFILE_REGISTRY.get(vendor)
    if existing is not None and existing is not profile_cls:
        raise ProfileRegistrationCollision(
            f"vendor profile {vendor!r} already registered to "
            f"{existing.__module__}.{existing.__qualname__}; refusing "
            f"{profile_cls.__module__}.{profile_cls.__qualname__}")
    _PROFILE_REGISTRY[vendor] = profile_cls


def resolve_profile(vendor: str) -> "DmtfProfile":
    """Return the profile instance for a vendor key, falling back to generic.

    The generic fallback is OBSERVABLE: it increments
    :data:`FALLBACK_COUNTS` and logs one structured line, so a misdetected
    box (the worst failure mode) is counted and visible, never silent.

    :param vendor: vendor key from detection or an explicit override.
    :return: the vendor's profile instance, or the generic profile.
    """
    profile_cls = _PROFILE_REGISTRY.get(vendor)
    if profile_cls is None:
        FALLBACK_COUNTS[vendor] = FALLBACK_COUNTS.get(vendor, 0) + 1
        logger.warning("vendor_profile fallback: vendor=%r has no registered "
                       "profile; using generic (count=%d)",
                       vendor, FALLBACK_COUNTS[vendor])
        profile_cls = _PROFILE_REGISTRY["generic"]
    return profile_cls.instance()


def profile_for_service_root(service_root: Optional[dict]) -> "DmtfProfile":
    """Resolve a profile from a parsed ServiceRoot document.

    Detection REUSES ``discover.classifier.classify_vendor`` (Oem child key ->
    ``@odata.type`` -> Manufacturer/Vendor text, never raises).

    :param service_root: parsed ``/redfish/v1`` document, or None.
    :return: the matching profile instance (generic when unidentifiable).
    """
    vendor = classify_vendor(service_root)
    profile = resolve_profile(vendor)
    logger.debug("vendor_profile resolved vendor=%s profile=%s",
                 vendor, type(profile).__name__)
    return profile


def profile_for_connection(host: str, port: int,
                           service_root_loader: Callable[[], Optional[dict]],
                           vendor_override: Optional[str] = None) -> "DmtfProfile":
    """Return the cached profile for a connection, probing at most once.

    :param host: BMC host or IP (the cache key with ``port``).
    :param port: BMC port.
    :param service_root_loader: zero-argument callable returning the parsed
        ServiceRoot (the manager passes its cached ``_service_root`` property,
        so a connection that already fetched the root pays ZERO extra GETs).
    :param vendor_override: explicit vendor (``--vendor``/``REDFISH_VENDOR``);
        skips the probe entirely when set.
    :return: the connection's profile instance.
    """
    key = (host, int(port))
    with _CACHE_LOCK:
        cached = _PROFILE_CACHE.get(key)
    if cached is not None:
        return cached
    if vendor_override:
        profile = resolve_profile(vendor_override)
    else:
        profile = profile_for_service_root(service_root_loader())
    with _CACHE_LOCK:
        return _PROFILE_CACHE.setdefault(key, profile)


def clear_profile_cache() -> None:
    """Reset the connection cache and fallback counters (test isolation)."""
    with _CACHE_LOCK:
        _PROFILE_CACHE.clear()
    FALLBACK_COUNTS.clear()


class DmtfProfile:
    """The DMTF lower-denominator chokepoint set — the shared profile base.

    Stateless; one instance per class (see :meth:`instance`). A vendor profile
    subclasses this and overrides ONLY the chokepoints that vendor genuinely
    diverges on. Method bodies marked CHIP are relocated from the managers by
    the implementation pass — the anchors are exact and the signatures are the
    contract; plumbing keeps them NotImplemented so a half-wired tree fails
    loudly instead of running half-moved semantics.
    """

    #: vendor key this profile serves; subclasses override.
    vendor: str = "generic"

    _instances: Dict[type, "DmtfProfile"] = {}

    @classmethod
    def instance(cls) -> "DmtfProfile":
        """Return the class's shared stateless instance.

        :return: the one instance of ``cls`` (profiles carry no state).
        """
        inst = cls._instances.get(cls)
        if inst is None:
            inst = cls._instances[cls] = cls()
        return inst

    def decode_status(self, status_code: int):
        """Fold an HTTP status code into the neutral RedfishApiRespond signal.

        CHIP: relocate the DMTF mapping (200/202/204 rows, NO 201=Created) —
        source: the neutral rows of ``idrac_manager.py`` ``_http_code_mapping``
        (:186-191) minus the Dell 201 row, per architecture.yaml state_decode.

        :param status_code: HTTP status from a BMC response.
        :return: the ``RedfishApiRespond`` signal.
        :raises NotImplementedError: until the implementation pass lands.
        """
        raise NotImplementedError("CHIP: relocate DMTF status map "
                                  "(idrac_manager.py:186-191 minus 201)")

    def error_handler(self, response, expected=None):
        """Decode a response into (signal, error) per DMTF semantics.

        CHIP: relocate the NEUTRAL ``default_error_handler`` body —
        source: ``redfish_manager.py:961`` (a @staticmethod today; every call
        site is self-bound, so the conversion is safe per the judge audit).

        :param response: the ``requests.Response`` to decode.
        :param expected: optional expected-status override(s).
        :return: the decoded signal (matching the current chokepoint contract).
        :raises NotImplementedError: until the implementation pass lands.
        """
        raise NotImplementedError("CHIP: relocate neutral default_error_handler "
                                  "(redfish_manager.py:961)")

    def parse_task_id(self, response):
        """Extract a task/job id from a 202-style response, DMTF form.

        CHIP: relocate Location-header parsing ONLY (``job_id_from_header``,
        redfish_manager.py:1038); the JID_ body-scrape at redfish_manager.py:1072
        moves to :class:`~redfish_ctl.dell_profile.DellProfile` — after this
        relocation the NEUTRAL layer contains no Dell literal (pays the
        known_debt row in architecture.yaml state_decode).

        :param response: the response carrying Location header and/or body.
        :return: the task id string, or None when the response names none.
        :raises NotImplementedError: until the implementation pass lands.
        """
        raise NotImplementedError("CHIP: relocate job_id_from_header "
                                  "(redfish_manager.py:1038); JID_ goes to Dell")

    def fetch_task(self, manager, task_id: str, **kwargs):
        """Poll one task to a terminal state, DMTF ``/TaskService/Tasks/{id}``.

        CHIP: relocate the neutral fetch path (redfish_manager.py:1164 region)
        — transport stays on ``manager`` (the profile is stateless; it borrows
        the connection, mirroring how commands call ``self.base_query``).

        :param manager: the connection's manager (transport provider).
        :param task_id: the task id to poll.
        :return: the terminal task payload (matching the current contract).
        :raises NotImplementedError: until the implementation pass lands.
        """
        raise NotImplementedError("CHIP: relocate neutral fetch_task "
                                  "(redfish_manager.py:1164)")

    def get_task_state(self, manager, task_id: str, **kwargs):
        """Read one task's current state, DMTF TaskState vocabulary.

        CHIP: neutral counterpart of the Dell override at idrac_manager.py:374.

        :param manager: the connection's manager (transport provider).
        :param task_id: the task id to read.
        :return: the task state (matching the current contract).
        :raises NotImplementedError: until the implementation pass lands.
        """
        raise NotImplementedError("CHIP: neutral get_task_state (DMTF TaskState)")


class SupermicroProfile(DmtfProfile):
    """Supermicro chokepoints — DMTF-conformant today; the seam for quirks.

    Evidenced Supermicro divergences land here (e.g. X10 legacy-era behavior);
    empty by design until a corpus/live trace proves a divergence.
    """
    vendor = "supermicro"


class HpeProfile(DmtfProfile):
    """HPE iLO chokepoints — DMTF-conformant today; the seam for quirks."""
    vendor = "hpe"


class OpenBmcProfile(DmtfProfile):
    """OpenBMC chokepoints — DMTF-conformant today; the seam for quirks."""
    vendor = "openbmc"


class NvidiaProfile(DmtfProfile):
    """NVIDIA (GB300-class) chokepoints — DMTF-conformant today; quirk seam."""
    vendor = "nvidia"


register_profile("generic", DmtfProfile)
register_profile("supermicro", SupermicroProfile)
register_profile("hpe", HpeProfile)
register_profile("openbmc", OpenBmcProfile)
register_profile("nvidia", NvidiaProfile)
