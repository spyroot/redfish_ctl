"""Scan a network segment for Redfish BMCs.

    idrac_ctl bmc-scan --subnet 10.43.3.0/24
    idrac_ctl bmc-scan --subnet 10.43.3.0/24 --port 443 --timeout 2 --workers 64

Probes every host in the given CIDR with a single unauthenticated
``GET https://<ip>/redfish/v1`` (the Redfish ServiceRoot) and reports the BMCs
that answer — open ServiceRoots and auth-locked (401/403) ones alike. The scan
engine lives in ``discovery/net_scan.py`` and also backs ``discovery --network``;
this command is the dedicated verb for it. Read-only host discovery: one GET per
host, no credentials, no mutation — for finding every Redfish BMC on a segment
before provisioning.

Vendor-neutral: any Redfish BMC (Dell/HPE/Supermicro/…) exposes /redfish/v1.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult
from .net_scan import scan_segment


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
            found = scan_segment(subnet, scan_port, scan_timeout, scan_workers)
        except ValueError as ve:
            return CommandResult([], None, None, f"invalid subnet '{subnet}': {ve}")

        from ..cmd_utils import save_if_needed
        save_if_needed(filename, found)
        return CommandResult(found, None, None, None)
