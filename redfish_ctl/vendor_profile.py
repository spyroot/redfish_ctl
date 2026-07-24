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
import time
from typing import Callable, Dict, Optional, Tuple, Type

import requests

from .cmd_exceptions import AuthenticationFailed, ResourceNotFound
from .discover.classifier import classify_vendor
from .redfish_exceptions import (
    ProfileRegistrationCollision,
    RedfishForbidden,
    RedfishUnauthorized,
)
from .redfish_shared import (
    RedfishApi,
    RedfishApiRespond,
    RedfishJson,
    RedfishJsonSpec,
)
from .redfish_task_state import TERMINAL_TASK_STATES, TaskState, TaskStatus
from .telemetry import tracing

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


def cached_profile(host: str, port: int) -> Optional["DmtfProfile"]:
    """Return the connection's cached profile without probing or caching.

    A pure peek: unlike :func:`profile_for_connection` it never loads a
    ServiceRoot and never writes the cache, so a caller can consult prior
    evidence (an earlier classification on this connection) before deciding
    how to resolve.

    :param host: BMC host or IP (the cache key with ``port``).
    :param port: BMC port.
    :return: the cached profile instance, or None when never classified.
    """
    with _CACHE_LOCK:
        return _PROFILE_CACHE.get((host, int(port)))


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

    #: DMTF status rows (no 201=Created — Created is a Dell addition; the
    #: neutral enum has no such member, per architecture.yaml state_decode).
    _status_map = {
        200: RedfishApiRespond.Ok,
        202: RedfishApiRespond.AcceptedTaskGenerated,
        204: RedfishApiRespond.Success,
    }

    def decode_status(self, status_code: int) -> RedfishApiRespond:
        """Fold an HTTP status code into the neutral RedfishApiRespond signal.

        The DMTF mapping (200/202/204 rows, any other 2xx folds to Success)
        relocated from the success branch of the neutral
        ``default_error_handler``; the Dell-only 201=Created row lives in
        :class:`~redfish_ctl.dell_profile.DellProfile`.

        :param status_code: HTTP status from a BMC response.
        :return: the ``RedfishApiRespond`` signal; ``Error`` for any non-2xx
            (the raising path is :meth:`error_handler`).
        """
        if 200 <= status_code < 300:
            return self._status_map.get(status_code, RedfishApiRespond.Success)
        return RedfishApiRespond.Error

    def error_handler(self, response, manager=None, expected=None):
        """Decode a response per DMTF semantics: fold 2xx, raise typed errors.

        Body relocated from the neutral ``RedfishManager.default_error_handler``
        (a @staticmethod, kept as a thin delegate to this method): 2xx folds via
        :meth:`decode_status`; 401/403 raise the neutral exceptions; every other
        error raises ``ResourceNotFound`` carrying the parsed RedfishError
        envelope.

        :param response: the ``requests.Response`` to decode.
        :param manager: optional connection manager; unused by the neutral
            profile (the Dell override records the parsed error on it).
        :param expected: reserved expected-status override; unused today.
        :return: the folded ``RedfishApiRespond`` for a 2xx status.
        :raises RedfishUnauthorized: on HTTP 401.
        :raises RedfishForbidden: on HTTP 403.
        :raises ResourceNotFound: on any other non-2xx status, carrying the
            parsed ``RedfishError`` envelope.
        """
        code = response.status_code
        if 200 <= code < 300:
            return self.decode_status(code)
        if code == 401:
            raise RedfishUnauthorized("Unauthorized access")
        if code == 403:
            raise RedfishForbidden("access forbidden")
        from .redfish_manager import RedfishManager
        error_msg = RedfishManager.parse_error(response)
        raise ResourceNotFound(error_msg)

    def parse_task_id(self, response) -> str:
        """Extract a task id from a 202-style response, Location header ONLY.

        The DMTF form per DSP0266: the task monitor URI rides the ``Location``
        header and the id is its last segment (relocated from
        ``job_id_from_header``). There is deliberately no body scrape here —
        the ``JID_`` scrape is Dell-only and lives in
        :class:`~redfish_ctl.dell_profile.DellProfile`, so the neutral layer
        holds no Dell literal.

        :param response: the response carrying the Location header.
        :return: the task id string, or an empty string when absent.
        """
        if response is None:
            return ""
        resp_hdr = response.headers
        if RedfishJsonSpec.Location not in resp_hdr:
            logging.debug("no task id in the response header "
                          "(not every api creates a task).")
            return ""
        location = resp_hdr[RedfishJsonSpec.Location]
        job_id = location.split("/")[-1]
        logging.debug(f"api returned task id {job_id} in the response header.")
        return job_id

    def fetch_task(self, manager, task_id: str, sleep_time: int = 10,
                   wait_for_state: Optional[TaskState] = None,
                   timeout: Optional[float] = None, **kwargs):
        """Poll one task to a terminal state, DMTF ``/TaskService/Tasks/{id}``.

        Body relocated from the neutral ``RedfishManager.fetch_task``:
        202 while running, 200 once a state is carried, 404/410 when a
        cancelled task is reaped, ``Retry-After`` honored when larger than
        ``sleep_time``. Transport stays on ``manager`` (the profile is
        stateless; it borrows the connection).

        :param manager: the connection's manager (transport provider).
        :param task_id: the ``Id`` of the task, as returned when created.
        :param sleep_time: seconds between polls; ``Retry-After`` wins when larger.
        :param wait_for_state: return as soon as this state is observed.
        :param timeout: optional wall-clock budget in seconds.
        :param kwargs: tolerated cross-vendor keywords (e.g. the Dell form's
            ``wait_for``); not consulted by the DMTF poll.
        :return: the last observed :class:`TaskState`, or ``None`` if the task
            never reported a recognised state.
        :raise AuthenticationFailed: if the service returns HTTP 401.
        """
        url = f"{manager._default_method}{manager.redfish_ip}{RedfishApi.Tasks}{task_id}"
        started = time.monotonic()
        task_state: Optional[TaskState] = None
        poll_count = 0

        # One INTERNAL span for the whole poll; each api_get_call below nests
        # as a CLIENT child automatically (the OTel context is the call stack).
        with tracing.poll_task_span() as poll_span:
            try:
                while True:
                    resp = manager.api_get_call(url, {})
                    poll_count += 1
                    code = resp.status_code

                    if code == 401:
                        raise AuthenticationFailed("task service returned 401.")
                    # A reaped/cancelled task monitor returns 410 Gone or 404
                    # Not Found; a 5xx ends the wait. Keep the last state seen.
                    if code in (404, 410) or code >= 500:
                        manager.logger.info(
                            f"task {task_id} monitor returned {code}; stopping poll."
                        )
                        break

                    state, _status = self.get_task_state(manager, resp)
                    if state is not None:
                        task_state = state
                    if wait_for_state is not None and task_state == wait_for_state:
                        break
                    if task_state in TERMINAL_TASK_STATES:
                        break

                    try:
                        retry_after = int(resp.headers.get("Retry-After", 0) or 0)
                    except (TypeError, ValueError):
                        retry_after = 0
                    delay = max(int(sleep_time or 0), retry_after)

                    if timeout is not None and (time.monotonic() - started) >= timeout:
                        manager.logger.info(
                            f"task {task_id} poll timed out after {timeout}s."
                        )
                        break
                    time.sleep(delay)
            finally:
                manager._set_poll_span_attributes(
                    poll_span, poll_count, sleep_time, started, task_state
                )

        return task_state

    def get_task_state(self, manager, resp):
        """Parse a DMTF ``#Task`` response into its state and status.

        Body relocated from the neutral ``RedfishManager.get_task_state``:
        reads the generic ``TaskState``/``TaskStatus`` properties and raises
        nothing on a missing key — an absent, non-JSON, or non-spec value maps
        to ``None`` so the caller keeps its last observed state.

        :param manager: the connection's manager (logging provider).
        :param resp: a requests.models.Response holding a ``#Task`` body.
        :return: a ``(TaskState, TaskStatus)`` tuple; either element is ``None``
            when the body is not a JSON object, the key is absent, or the value
            is not a DMTF-defined enum member.
        """
        try:
            data = resp.json()
        except requests.exceptions.JSONDecodeError as json_err:
            manager.logger.debug(f"task response carried no json body: {json_err}")
            return None, None
        if not isinstance(data, dict):
            return None, None

        def _coerce(enum_cls, value):
            """Return the enum member for a wire value, or None if not a member.

            :param enum_cls: the enum class to coerce into (TaskState / TaskStatus).
            :param value: the raw wire value read from the #Task body.
            :return: the matching enum member, or None when value is not a member.
            """
            try:
                return enum_cls(value)
            except ValueError:
                return None

        return (
            _coerce(TaskState, data.get(RedfishJson.TaskState)),
            _coerce(TaskStatus, data.get(RedfishJson.TaskStatus)),
        )

    def get_job(self, manager, job_id: str, data_type: str = "json",
                do_async: bool = False):
        """Read one task from the DMTF ``TaskService`` (the neutral job read).

        The vendor-neutral counterpart of the Dell OEM job read: a task on a
        DMTF service lives at ``/redfish/v1/TaskService/Tasks/{id}`` — there is
        no ``/Oem/Dell/Jobs`` on a non-Dell box, so routing a job read through
        the profile is what keeps a Dell OEM URL off foreign BMCs.

        :param manager: the connection's manager (transport provider).
        :param job_id: the task id to read.
        :param data_type: accepted for signature parity with the Dell form.
        :param do_async: when True the read subscribes to an event loop.
        :return: the task payload dict from the service.
        """
        r = f"{RedfishApi.Tasks}{job_id}"
        return manager.base_query(r, do_expanded=True, do_async=do_async).data


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
