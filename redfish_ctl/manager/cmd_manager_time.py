"""Read or set the BMC (Manager) clock via Redfish ``DateTime``.

    redfish_ctl manager-time                        # read each manager's clock
    redfish_ctl manager-time --now                  # set to this host's current UTC
    redfish_ctl manager-time --set 2026-07-02T20:00:00+00:00
    redfish_ctl manager-time --now --offset +00:00

Reads by default; it **writes only** when ``--now`` or ``--set`` is given (a
deliberate mutation, same read-first pattern as ``discovery``/``bmc-scan``).
Vendor-neutral: it walks ``discover_manager_ids()`` and PATCHes
``Managers/<id> {DateTime[, DateTimeLocalOffset]}``. Useful when the BMC RTC has
drifted and NTP is unavailable (common on thin / early Redfish such as Supermicro
X10 Redfish 1.0.1), which otherwise skews every log/SEL timestamp on the box.

Author Mus spyroot@gmail.com
"""
import datetime
from abc import abstractmethod
from typing import Optional

from ..base_manager import CommandBase
from ..command_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult


def build_time_payload(set_now: bool,
                       set_datetime: Optional[str],
                       set_offset: Optional[str]) -> Optional[dict]:
    """Build the ``PATCH`` body for a Manager DateTime write, or ``None`` to read.

    :param set_now: set the clock to the caller host's current UTC time.
    :param set_datetime: an explicit ISO-8601 DateTime to set (wins over --now).
    :param set_offset: optional ``DateTimeLocalOffset`` (e.g. ``+00:00``).
    :return: a payload dict when a write was requested, else ``None``.
    """
    if not set_now and not set_datetime:
        return None
    if set_datetime:
        value = set_datetime
    else:
        # Current UTC in the offset form Redfish expects (…+00:00, not "Z").
        value = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S+00:00")
    payload = {"DateTime": value}
    if set_offset:
        payload["DateTimeLocalOffset"] = set_offset
    return payload


class ManagerTime(CommandBase,
                  scm_type=ApiRequestType.ManagerTime,
                  name='manager-time',
                  metaclass=Singleton):
    """Read or set each Manager's Redfish DateTime clock."""

    def __init__(self, *args, **kwargs):
        super(ManagerTime, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register ``manager-time`` (read by default; --now/--set to write)."""
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            '--now', action='store_true', required=False, dest='set_now',
            default=False, help="set the BMC clock to this host's current UTC time")
        cmd_parser.add_argument(
            '--set', required=False, dest='set_datetime', type=str, default=None,
            help="set the BMC clock to an explicit ISO-8601 time, "
                 "e.g. 2026-07-02T20:00:00+00:00")
        cmd_parser.add_argument(
            '--offset', required=False, dest='set_offset', type=str, default=None,
            help="optional DateTimeLocalOffset to set alongside, e.g. +00:00")
        return cmd_parser, "manager-time", "read or set the BMC (Manager) clock"

    def execute(self,
                set_now: Optional[bool] = False,
                set_datetime: Optional[str] = None,
                set_offset: Optional[str] = None,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Read each Manager's DateTime; with --now/--set, PATCH it and read back."""
        payload = build_time_payload(set_now, set_datetime, set_offset)

        try:
            manager_ids = self.discover_manager_ids() or []
        except Exception as e:
            return CommandResult([], None, None, f"failed to discover managers: {e}")
        if not manager_ids:
            return CommandResult([], None, None, "no Managers found")

        rows = []
        for mgr_uri in manager_ids:
            mgr_uri = mgr_uri.rstrip("/")
            mgr_id = mgr_uri.rsplit("/", 1)[-1]
            before = (self.base_query(mgr_uri, do_async=do_async).data or {})
            row = {
                "Manager": mgr_id,
                "DateTime": before.get("DateTime"),
                "DateTimeLocalOffset": before.get("DateTimeLocalOffset"),
            }
            if payload is not None:
                row["Requested"] = payload["DateTime"]
                res, status = self.base_patch(mgr_uri, payload=payload)
                row["WriteStatus"] = str(status)
                row["WriteError"] = res.error
                after = (self.base_query(mgr_uri, do_async=do_async).data or {})
                row["DateTime"] = after.get("DateTime")
                row["DateTimeLocalOffset"] = after.get("DateTimeLocalOffset")
            rows.append(row)

        from ..cmd_utils import save_if_needed
        save_if_needed(filename, rows)
        return CommandResult(rows, None, None, None)
