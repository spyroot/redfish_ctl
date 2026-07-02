"""Wait for the BMC Redfish service to be reachable (e.g. after a reboot).

    idrac_ctl wait                          # wait up to 300s for the ServiceRoot to respond
    idrac_ctl wait --timeout 600 --interval 5
    idrac_ctl wait --reboot-cycle           # wait to go DOWN then come back (confirm a reboot)

Polls ``GET /redfish/v1/`` until it returns any HTTP status (200/401/403 all mean
the BMC is up). Vendor-neutral. Use it after ``manager-reboot`` or a power action
instead of a hand-rolled sleep loop —
``idrac_ctl manager-reboot && idrac_ctl wait --reboot-cycle``.

Author Mus spyroot@gmail.com
"""
import time
from abc import abstractmethod
from typing import Optional

import requests

from .idrac_manager import IDracManager
from .idrac_shared import ApiRequestType, Singleton
from .redfish_manager import CommandResult


def probe_reachable(url: str, auth, verify: bool, timeout: float) -> bool:
    """True if the ServiceRoot returns ANY HTTP status (the BMC is up), else False.

    Any HTTP response — 200, 401, or 403 — means the Redfish service answered, so
    the BMC is reachable. A connection error / timeout means it is not up yet.
    """
    try:
        requests.get(url, auth=auth, verify=verify, timeout=timeout)
        return True
    except Exception:
        return False


def wait_reachable(url: str, auth, verify: bool,
                   timeout: float = 300.0, interval: float = 5.0,
                   reboot_cycle: bool = False) -> dict:
    """Poll ``url`` until reachable, bounded by ``timeout``. Reusable by any command.

    With ``reboot_cycle`` it first waits for the service to go DOWN (confirming a
    reset actually started) and then for it to come back UP. Returns
    ``{reachable, waited_s[, went_down]}``. Probes at least once even if the
    timeout is tiny.
    """
    probe_timeout = max(1.0, min(interval or 5.0, 5.0))
    interval = max(0.0, interval or 0.0)
    start = time.monotonic()
    deadline = start + max(0.0, timeout or 0.0)

    out: dict = {}
    if reboot_cycle:
        went_down = False
        while True:
            if not probe_reachable(url, auth, verify, probe_timeout):
                went_down = True
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(interval)
        out["went_down"] = went_down

    reachable = False
    while True:
        if probe_reachable(url, auth, verify, probe_timeout):
            reachable = True
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(interval)

    out["reachable"] = reachable
    out["waited_s"] = round(time.monotonic() - start, 1)
    return out


class WaitReady(IDracManager,
                scm_type=ApiRequestType.WaitReady,
                name='wait',
                metaclass=Singleton):
    """Poll the Redfish ServiceRoot until the BMC is reachable (bounded by a timeout)."""

    def __init__(self, *args, **kwargs):
        super(WaitReady, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        cmd = cls.base_parser()
        cmd.add_argument('--timeout', required=False, type=float, dest='wait_timeout', default=300.0,
                         help="max seconds to wait for the BMC (default 300)")
        cmd.add_argument('--interval', required=False, type=float, dest='wait_interval', default=5.0,
                         help="seconds between polls (default 5)")
        cmd.add_argument('--reboot-cycle', action='store_true', required=False,
                         dest='wait_reboot_cycle', default=False,
                         help="first wait for the BMC to go DOWN, then wait for it to come back UP")
        return cmd, "wait", "wait for the BMC Redfish service to be reachable"

    def execute(self,
                wait_timeout: Optional[float] = 300.0,
                wait_interval: Optional[float] = 5.0,
                wait_reboot_cycle: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Poll the ServiceRoot until reachable (optionally after a down phase)."""
        scheme = "http" if self._is_http else "https"
        url = f"{scheme}://{self.redfish_ip}:{self._port}/redfish/v1/"
        auth = (self._username, self._password) if self._username else None
        result = wait_reachable(url, auth, self._is_verify_cert,
                                wait_timeout, wait_interval, wait_reboot_cycle)
        result["target"] = self.redfish_ip
        error = None if result["reachable"] else f"BMC not reachable within {wait_timeout}s"
        return CommandResult(result, None, None, error)
