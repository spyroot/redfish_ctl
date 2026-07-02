"""Scan a network segment for Redfish BMCs.

    idrac_ctl bmc-scan --subnet 10.43.3.0/24
    idrac_ctl bmc-scan --subnet 10.43.3.0/24 --port 443 --timeout 2 --workers 64

Probes every host in the given CIDR with a single unauthenticated
``GET https://<ip>/redfish/v1`` (the Redfish ServiceRoot) and reports the ones
that answer, with {IP, RedfishVersion, Product, Vendor, Managers, Systems}. This
is read-only host discovery — one GET per host, no credentials, no mutation — for
finding every server with a Redfish BMC on a segment before provisioning.

Vendor-neutral: any Redfish BMC (Dell/HPE/Supermicro/…) exposes /redfish/v1.

Author Mus spyroot@gmail.com
"""
import ipaddress
from abc import abstractmethod
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import requests

from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult


class BmcScan(IDracManager,
             scm_type=ApiRequestType.BmcScan,
             name='bmc-scan',
             metaclass=Singleton):
    """Scan a CIDR for hosts exposing a Redfish ServiceRoot."""

    def __init__(self, *args, **kwargs):
        super(BmcScan, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``bmc-scan`` subcommand and its scan flags."""
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            '--subnet', required=False, dest='subnet', type=str, default=None,
            help="network segment to scan, CIDR e.g. 10.43.3.0/24 (or a single 10.43.3.209/32)")
        cmd_parser.add_argument(
            '--port', required=False, dest='scan_port', type=int, default=443,
            help="HTTPS port to probe (default 443)")
        cmd_parser.add_argument(
            '--timeout', required=False, dest='scan_timeout', type=float, default=2.0,
            help="per-host probe timeout in seconds (default 2)")
        cmd_parser.add_argument(
            '--workers', required=False, dest='scan_workers', type=int, default=64,
            help="concurrent probes (default 64)")
        return cmd_parser, "bmc-scan", "command scan a CIDR for Redfish BMCs"

    @staticmethod
    def _probe(ip, port, timeout):
        """One unauthenticated ServiceRoot GET; return a BMC row or None.

        A 200 with RedfishVersion is an open ServiceRoot. A 401/403 still means a
        Redfish service is there but locks the ServiceRoot behind auth (a real
        BMC) — a 404/refused/timeout is not Redfish. So auth-locked BMCs are still
        detected, just marked Auth=required (query them once creds are supplied).
        """
        url = f"https://{ip}:{port}/redfish/v1"
        try:
            resp = requests.get(url, verify=False, timeout=timeout)
        except Exception:
            return None
        if resp.status_code in (401, 403):
            return {"IP": ip, "RedfishVersion": None, "Product": None, "Vendor": [],
                    "Managers": None, "Systems": None, "Auth": "required"}
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
        return {
            "IP": ip,
            "RedfishVersion": data.get("RedfishVersion"),
            "Product": data.get("Product"),
            "Vendor": list((data.get("Oem") or {}).keys()),
            "Managers": managers.get("@odata.id") if isinstance(managers, dict) else None,
            "Systems": systems.get("@odata.id") if isinstance(systems, dict) else None,
            "Auth": "open",
        }

    def execute(self,
                subnet: Optional[str] = None,
                scan_port: Optional[int] = 443,
                scan_timeout: Optional[float] = 2.0,
                scan_workers: Optional[int] = 64,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Expand the CIDR and probe each host concurrently for a Redfish BMC."""
        if not subnet:
            return CommandResult(
                [], None, None,
                "provide --subnet as a CIDR, e.g. --subnet 10.43.3.0/24")
        try:
            net = ipaddress.ip_network(subnet, strict=False)
            hosts = [str(h) for h in (net.hosts() or [net.network_address])]
            if not hosts:                       # /32 or /31
                hosts = [str(net.network_address)]
        except ValueError as ve:
            return CommandResult([], None, None, f"invalid subnet '{subnet}': {ve}")

        workers = max(1, min(scan_workers or 64, 256))
        found = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for row in pool.map(lambda ip: self._probe(ip, scan_port, scan_timeout), hosts):
                if row:
                    found.append(row)

        from ..cmd_utils import save_if_needed
        save_if_needed(filename, found)
        return CommandResult(found, None, None, None)
