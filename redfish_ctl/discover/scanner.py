"""Async subnet scan for reachable Redfish services.

:func:`scan_subnet` probes many hosts concurrently for a Redfish service root and
returns the ones that answer. It performs **no** network I/O itself and stores no
credentials: the caller injects an async ``get`` callable that knows how to fetch
``/redfish/v1/`` for a single host (with whatever auth/TLS policy the caller
chose). That keeps this module product-neutral and fully offline-testable — tests
pass a fake async GET.

Concurrency is bounded by an :class:`asyncio.Semaphore` so a large host list
cannot open an unbounded number of sockets at once.

Author Mus spyroot@gmail.com
"""
import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

from redfish_ctl.discover.classifier import classify_vendor

# The well-known Redfish service root path. Probed read-only.
REDFISH_ROOT_PATH = "/redfish/v1/"

# Signature of the injected fetcher: given a host, return the parsed ServiceRoot
# dict, or ``None`` when the host is not a reachable Redfish service. The callable
# owns its own transport, auth, and error handling.
AsyncGet = Callable[[str], Awaitable[Optional[Dict[str, Any]]]]


@dataclass(frozen=True)
class DiscoveredService:
    """A reachable Redfish service found during a scan.

    :ivar ip: the host address that answered.
    :ivar vendor: classification from :func:`classify_vendor`.
    :ivar product: ``Product`` field from the ServiceRoot, if present.
    :ivar redfish_version: ``RedfishVersion`` field, if present.
    """

    ip: str
    vendor: str
    product: Optional[str]
    redfish_version: Optional[str]

    def as_dict(self) -> Dict[str, Optional[str]]:
        """Return the record as a plain dict (handy for rendering/serialization).

        :return: dict with keys ``ip``, ``vendor``, ``product``, and
            ``redfish_version``.
        """
        return {
            "ip": self.ip,
            "vendor": self.vendor,
            "product": self.product,
            "redfish_version": self.redfish_version,
        }


def _service_from_root(
        ip: str, service_root: Optional[Dict[str, Any]]) -> Optional[DiscoveredService]:
    """Build a :class:`DiscoveredService` from a fetched ServiceRoot, or ``None``.

    A falsy/non-mapping root means the host is not a usable Redfish service, so we
    return ``None`` and the caller drops it from the results.

    :param ip: host address that produced this ServiceRoot.
    :param service_root: parsed ``/redfish/v1/`` dict, or ``None`` when the host
        did not answer with a usable Redfish document.
    :return: a :class:`DiscoveredService` for a non-empty mapping root, else
        ``None``.
    """
    if not isinstance(service_root, dict) or not service_root:
        return None
    product = service_root.get("Product")
    redfish_version = service_root.get("RedfishVersion")
    return DiscoveredService(
        ip=ip,
        vendor=classify_vendor(service_root),
        product=product if isinstance(product, str) else None,
        redfish_version=redfish_version if isinstance(redfish_version, str) else None,
    )


async def _probe_host(
        ip: str,
        get: AsyncGet,
        semaphore: asyncio.Semaphore) -> Optional[DiscoveredService]:
    """Probe a single host under the concurrency semaphore.

    Any exception raised by the injected ``get`` (timeout, connection refused,
    bad TLS, non-JSON body) is swallowed so one unreachable host never fails the
    whole scan; that host is simply reported as not discovered (``None``).

    :param ip: host address to probe.
    :param get: async callable that fetches ``/redfish/v1/`` for one host.
    :param semaphore: bounds the number of concurrent probes in flight.
    :return: a :class:`DiscoveredService` when the host answers with a usable
        ServiceRoot, else ``None``.
    """
    async with semaphore:
        try:
            service_root = await get(ip)
        except Exception:
            return None
    return _service_from_root(ip, service_root)


async def scan_subnet(
        hosts: Sequence[str],
        get: AsyncGet,
        *,
        concurrency: int = 32) -> List[DiscoveredService]:
    """Probe ``hosts`` for reachable Redfish services, concurrently.

    :param hosts: host addresses to probe (e.g. an expanded subnet). Duplicates
        and falsy entries are ignored; order of the returned list follows the
        order of the first occurrence of each host in ``hosts``.
    :param get: async callable fetching ``/redfish/v1/`` for one host and
        returning the parsed ServiceRoot dict, or ``None`` when not reachable.
        This is the only thing that touches the network — the module never calls
        out on its own and never holds credentials.
    :param concurrency: maximum number of in-flight probes; must be >= 1. Bounds
        socket/file-descriptor usage on large host lists.
    :return: a list of :class:`DiscoveredService`, one per reachable host, in the
        input order described above.
    :raises ValueError: if ``concurrency`` < 1.

    Hosts that error or do not answer with a Redfish service root are silently
    omitted. The scan is read-only; it only issues GETs through ``get``.
    """
    if concurrency < 1:
        raise ValueError("concurrency must be >= 1")

    # Preserve input order while dropping duplicates and empty entries.
    ordered_hosts: List[str] = []
    seen = set()
    for host in hosts:
        if not host or host in seen:
            continue
        seen.add(host)
        ordered_hosts.append(host)

    if not ordered_hosts:
        return []

    semaphore = asyncio.Semaphore(concurrency)
    results = await asyncio.gather(
        *(_probe_host(host, get, semaphore) for host in ordered_hosts)
    )
    return [svc for svc in results if svc is not None]
