"""Credential-less Redfish BMC discovery across a network segment.

The scan engine shared by the ``bmc-scan`` command and ``discovery --network``.
It expands a CIDR and issues one unauthenticated ``GET /redfish/v1`` per host,
concurrently:

* a ``200`` with ``RedfishVersion`` is an **open** ServiceRoot — the vendor is
  classified from it;
* a ``401``/``403`` is an **auth-locked** BMC — the ServiceRoot exists but
  requires a login, so it is still reported (``Auth="required"``); this is the
  case for BMCs that lock even the service root, which a 200-only probe misses;
* anything else (``404``, connection refused, timeout, non-JSON body) is not a
  BMC and is dropped.

Read-only: one GET per host, no credentials, no mutation. Vendor labeling reuses
the product-neutral classifier from the sibling ``idrac_ctl.discover`` package.

Author Mus spyroot@gmail.com
"""
import ipaddress
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

import requests

from idrac_ctl.discover.classifier import classify_vendor

# Well-known Redfish service root; probed read-only and unauthenticated.
REDFISH_ROOT_PATH = "/redfish/v1"
# Hard cap on concurrent probes regardless of the requested worker count, so a
# large /16 cannot open an unbounded number of sockets at once.
MAX_WORKERS = 256


def expand_cidr(subnet: str) -> List[str]:
    """Expand a CIDR (or single IP / ``/32`` / ``/31``) to host addresses.

    :param subnet: a network in CIDR form (``192.168.1.0/24``) or a bare address.
    :return: the list of host addresses to probe.
    :raises ValueError: if ``subnet`` is not a valid network/address.
    """
    net = ipaddress.ip_network(subnet, strict=False)
    hosts = [str(h) for h in net.hosts()]
    # /32 and /31 expose no usable ``hosts()`` — fall back to the network address
    # itself so a single-host probe (e.g. 10.43.3.209/32) still works.
    return hosts or [str(net.network_address)]


def probe_host(ip: str, port: int = 443, timeout: float = 2.0) -> Optional[dict]:
    """One unauthenticated ServiceRoot GET; return a BMC row or ``None``.

    :param ip: host address to probe.
    :param port: HTTPS port (default 443).
    :param timeout: per-host connect/read timeout in seconds.
    :return: a BMC row ``{IP, Vendor, Product, RedfishVersion, Managers,
             Systems, Auth}`` for an open (200) or auth-locked (401/403)
             ServiceRoot, else ``None``.
    """
    url = f"https://{ip}:{port}{REDFISH_ROOT_PATH}"
    try:
        resp = requests.get(url, verify=False, timeout=timeout)
    except Exception:
        return None
    if resp.status_code in (401, 403):
        # A locked ServiceRoot: definitely a Redfish BMC, but we cannot read the
        # vendor without creds — report it so it can be re-queried authenticated.
        return {"IP": ip, "Vendor": "unknown", "Product": None,
                "RedfishVersion": None, "Managers": None, "Systems": None,
                "Auth": "required"}
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    if not isinstance(data, dict) or "RedfishVersion" not in data:
        return None
    managers = data.get("Managers") or {}
    systems = data.get("Systems") or {}
    product = data.get("Product")
    return {
        "IP": ip,
        "Vendor": classify_vendor(data),
        "Product": product if isinstance(product, str) else None,
        "RedfishVersion": data.get("RedfishVersion"),
        "Managers": managers.get("@odata.id") if isinstance(managers, dict) else None,
        "Systems": systems.get("@odata.id") if isinstance(systems, dict) else None,
        "Auth": "open",
    }


def scan_segment(subnet: str, port: int = 443, timeout: float = 2.0,
                 workers: int = 64) -> List[dict]:
    """Expand ``subnet`` and probe every host concurrently for a Redfish BMC.

    :param subnet: network to scan, in CIDR form or a bare address.
    :param port: HTTPS port to probe on each host.
    :param timeout: per-host probe timeout in seconds.
    :param workers: desired concurrency (clamped to ``[1, MAX_WORKERS]``).
    :return: detected BMC rows (open + auth-locked), in address order.
    :raises ValueError: if ``subnet`` is not a valid CIDR/address.
    """
    hosts = expand_cidr(subnet)
    workers = max(1, min(workers or 64, MAX_WORKERS))
    found: List[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for row in pool.map(lambda ip: probe_host(ip, port, timeout), hosts):
            if row:
                found.append(row)
    return found
