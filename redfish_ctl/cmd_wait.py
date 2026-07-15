"""Wait for the BMC Redfish service to be reachable (e.g. after a reboot).

    redfish_ctl wait                          # wait up to 300s for the ServiceRoot to respond
    redfish_ctl wait --timeout 600 --interval 5
    redfish_ctl wait --reboot-cycle           # wait to go DOWN then come back (confirm a reboot)

Polls ``GET /redfish/v1/`` until it returns any HTTP status (200/401/403 all mean
the BMC is up). Vendor-neutral. Use it after ``manager-reboot`` or a power action
instead of a hand-rolled sleep loop —
``redfish_ctl manager-reboot && redfish_ctl wait --reboot-cycle``.

Author Mus spyroot@gmail.com
"""
import time
from abc import abstractmethod
from typing import Optional

import requests

from .redfish_manager_base import RedfishManagerBase
from .redfish_manager_shared import ApiRequestType, Singleton
from .redfish_manager import CommandResult


def probe_reachable(url: str, auth, verify: bool, timeout: float) -> bool:
    """True if the ServiceRoot returns ANY HTTP status (the BMC is up), else False.

    Any HTTP response — 200, 401, or 403 — means the Redfish service answered, so
    the BMC is reachable. A connection error / timeout means it is not up yet.

    :param url: ServiceRoot URL to probe.
    :param auth: requests auth tuple ``(username, password)``, or None for anonymous.
    :param verify: whether to verify the TLS certificate.
    :param timeout: per-request timeout in seconds.
    :return: True if the service answered with any HTTP status, else False.
    """
    try:
        requests.get(url, auth=auth, verify=verify, timeout=timeout)
        return True
    except Exception:
        return False


def wait_for(predicate,
             description: str = "condition",
             timeout: float = 300.0,
             interval: float = 5.0,
             invert_first: bool = False) -> dict:
    """Poll a no-arg ``predicate() -> bool`` until it is True, bounded by ``timeout``.

    A **generic** wait any command can consume for any event/condition — the BMC
    becoming reachable, a power state reached, virtual media mounted, a job/task
    reaching a state, a file finishing copying. The caller supplies the predicate
    plus a human ``description`` of what is being awaited; the result echoes it so
    the operator sees exactly what was (or was not) satisfied. A predicate that
    raises is treated as "not yet". Probes at least once even for a tiny timeout.

    ``invert_first`` first waits for the predicate to be False (e.g. the BMC to go
    DOWN) before waiting for it to be True — the down-then-up reboot pattern.

    :param predicate: no-arg callable returning bool; polled until it is True.
    :param description: human label for what is being awaited; echoed in the result.
    :param timeout: max seconds to keep polling before giving up.
    :param interval: seconds to sleep between probes.
    :param invert_first: first wait for the predicate to be False, then for True.
    :return: ``{waiting_for, satisfied, waited_s[, precondition_met]}``
    """
    def probe() -> bool:
        """Evaluate ``predicate`` once, treating any raised exception as False.

        :return: the predicate's boolean result, or False if it raised.
        """
        try:
            return bool(predicate())
        except Exception:
            return False

    interval = max(0.0, interval or 0.0)
    start = time.monotonic()
    deadline = start + max(0.0, timeout or 0.0)
    out: dict = {"waiting_for": description}

    if invert_first:
        precondition_met = False
        while True:
            if not probe():
                precondition_met = True
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(interval)
        out["precondition_met"] = precondition_met

    satisfied = False
    while True:
        if probe():
            satisfied = True
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(interval)

    out["satisfied"] = satisfied
    out["waited_s"] = round(time.monotonic() - start, 1)
    return out


def wait_reachable(url: str, auth, verify: bool,
                   timeout: float = 300.0, interval: float = 5.0,
                   reboot_cycle: bool = False) -> dict:
    """Wait for the Redfish ServiceRoot at ``url`` to respond (the BMC is up).

    A thin wrapper over :func:`wait_for` with a reachability predicate; keeps the
    ``reachable`` / ``went_down`` keys its callers (the ``wait`` command,
    ``manager-reboot --wait``) expect, and carries the ``waiting_for`` label.

    :param url: ServiceRoot URL to poll.
    :param auth: requests auth tuple ``(username, password)``, or None for anonymous.
    :param verify: whether to verify the TLS certificate.
    :param timeout: max seconds to wait for the service.
    :param interval: seconds between polls.
    :param reboot_cycle: first wait for the BMC to go DOWN, then for it to come back UP.
    :return: ``{waiting_for, reachable, waited_s[, went_down]}``.
    """
    probe_timeout = max(1.0, min(interval or 5.0, 5.0))
    res = wait_for(lambda: probe_reachable(url, auth, verify, probe_timeout),
                   description=f"BMC reachable at {url}",
                   timeout=timeout, interval=interval, invert_first=reboot_cycle)
    out = {"waiting_for": res["waiting_for"],
           "reachable": res["satisfied"], "waited_s": res["waited_s"]}
    if reboot_cycle:
        out["went_down"] = res.get("precondition_met", False)
    return out


class WaitReady(RedfishManagerBase,
                scm_type=ApiRequestType.WaitReady,
                name='wait',
                metaclass=Singleton):
    """Poll the Redfish ServiceRoot until the BMC is reachable (bounded by a timeout)."""

    def __init__(self, *args, **kwargs):
        """Initialize the wait command."""
        super(WaitReady, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``wait`` command parser and its flags.

        :return: tuple of (ArgumentParser, command name, command help).
        """
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
        """Poll the ServiceRoot until reachable (optionally after a down phase).

        :param wait_timeout: max seconds to wait for the BMC (default 300).
        :param wait_interval: seconds between polls (default 5).
        :param wait_reboot_cycle: first wait for the BMC to go DOWN, then come back UP.
        :return: CommandResult with the reachability result (``reachable``, ``waited_s``,
            ``target``); its error is set when the BMC is not reachable within the timeout.
        """
        scheme = "http" if self._is_http else "https"
        url = f"{scheme}://{self.redfish_ip}:{self._port}/redfish/v1/"
        auth = (self._username, self._password) if self._username else None
        result = wait_reachable(url, auth, self._is_verify_cert,
                                wait_timeout, wait_interval, wait_reboot_cycle)
        result["target"] = self.redfish_ip
        error = None if result["reachable"] else f"BMC not reachable within {wait_timeout}s"
        return CommandResult(result, None, None, error)
