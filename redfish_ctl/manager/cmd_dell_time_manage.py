"""Query or set Dell service-processor time through DellTimeService.ManageTime.

    redfish_ctl dell-time-manage
    redfish_ctl dell-time-manage --dry_run
    redfish_ctl dell-time-manage --set-time 2026-07-18T03:00:00+00:00
    redfish_ctl dell-time-manage --set-time 2026-07-18T03:00:00+00:00 --confirm

``#DellTimeService.ManageTime`` uses POST for both a read-style query and a
volatile time write. This command sends the read payload by default and requires
``--confirm`` before it sends a set-time payload.

Author Mus spyroot@gmail.com
"""
import datetime
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton
from ..redfish_shared import RedfishApi

_DELL_TIME_ACTION = "#DellTimeService.ManageTime"
_DELL_TIME_FALLBACK = (
    f"{RedfishApi.Version}/Managers/iDRAC.Embedded.1/Oem/Dell/DellTimeService"
)


class DellTimeManage(RedfishManagerBase,
                     scm_type=ApiRequestType.DellTimeManage,
                     name="dell-time-manage",
                     metaclass=Singleton):
    """Query or set Dell service-processor time via DellTimeService."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-time-manage command."""
        super(DellTimeManage, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``dell-time-manage`` subcommand.

        :param cls: command class used to build the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--manager",
            default=None,
            help="manager Id or URI when more than one Dell manager advertises "
                 "DellTimeService",
        )
        cmd_parser.add_argument(
            "--set-time",
            dest="set_time",
            default=None,
            help="ISO-8601 DateTime to set through DellTimeService.ManageTime",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            default=False,
            help="send the set-time POST; reads do not require confirmation",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="resolve the target and payload without POSTing",
        )
        return (
            cmd_parser,
            "dell-time-manage",
            "command query or set Dell service-processor time",
        )

    @staticmethod
    def _link(data, key):
        """Return a Redfish ``@odata.id`` link value from a resource body.

        :param data: Redfish resource body.
        :param key: link property name.
        :return: link URI, or None when absent.
        """
        link = data.get(key) if isinstance(data, dict) else None
        return link.get("@odata.id") if isinstance(link, dict) else None

    @staticmethod
    def _oem_dell(data):
        """Return the ``Links.Oem.Dell`` block from a Manager resource.

        :param data: Manager resource body.
        :return: Dell OEM links block, or an empty dict.
        """
        links = data.get("Links") if isinstance(data, dict) else None
        oem = links.get("Oem") if isinstance(links, dict) else None
        dell = oem.get("Dell") if isinstance(oem, dict) else None
        return dell if isinstance(dell, dict) else {}

    def _get(self, uri, do_async, optional=False):
        """GET a Redfish resource body.

        :param uri: Redfish resource URI.
        :param do_async: issue the GET over the async path when True.
        :param optional: return an empty object instead of failing on read errors.
        :return: parsed Redfish object body.
        :raises InvalidArgument: when a required read fails or is not an object.
        """
        try:
            data = self.base_query(uri, do_async=do_async).data or {}
        except Exception as exc:
            if optional:
                return {}
            raise InvalidArgument(f"failed to read {uri}: {exc}") from exc
        if not isinstance(data, dict):
            if optional:
                return {}
            raise InvalidArgument(f"unexpected response from {uri}: expected object")
        return data

    def _service_uri_for_manager(self, manager_uri, do_async):
        """Resolve a manager's DellTimeService URI.

        :param manager_uri: Manager resource URI.
        :param do_async: issue the Manager GET over the async path when True.
        :return: DellTimeService URI.
        """
        manager = self._get(manager_uri, do_async, optional=True)
        service_uri = self._link(self._oem_dell(manager), "DellTimeService")
        if service_uri:
            return service_uri
        if manager_uri.rstrip("/").endswith("/iDRAC.Embedded.1"):
            return _DELL_TIME_FALLBACK
        return None

    def _discover_rows(self, do_async):
        """Discover managers that advertise DellTimeService.ManageTime.

        :param do_async: issue discovery reads over the async path when True.
        :return: list of target rows.
        """
        rows = []
        for manager_uri in self.discover_manager_ids() or []:
            manager_uri = manager_uri.rstrip("/")
            service_uri = self._service_uri_for_manager(manager_uri, do_async)
            if not service_uri:
                continue
            service = self._get(service_uri, do_async, optional=True)
            target = self._flatten_action_targets(service).get(_DELL_TIME_ACTION)
            if not target:
                continue
            rows.append({
                "Manager": manager_uri.rsplit("/", 1)[-1],
                "ManagerUri": manager_uri,
                "Service": service_uri,
                "Target": target,
            })
        return rows

    @staticmethod
    def _select_rows(rows, manager):
        """Filter target rows by manager selector.

        :param rows: discovered DellTimeService target rows.
        :param manager: optional manager Id or URI.
        :return: matching rows.
        :raises InvalidArgument: when the selector does not match.
        """
        if not manager:
            return rows
        selector = manager.strip()
        if not selector:
            raise InvalidArgument("manager selector cannot be empty")
        folded = selector.lower()
        matches = [
            row for row in rows
            if folded in {row["Manager"].lower(), row["ManagerUri"].lower()}
        ]
        if not matches:
            available = [row["Manager"] for row in rows]
            raise InvalidArgument(
                f"no DellTimeService target for manager '{manager}'; "
                f"available: {available}"
            )
        return matches

    @staticmethod
    def _validate_time_data(value):
        """Validate and normalize a requested Redfish DateTime string.

        :param value: user-supplied DateTime.
        :return: stripped DateTime string.
        :raises InvalidArgument: when the value is empty or not ISO-8601-like.
        """
        text = str(value or "").strip()
        if not text:
            raise InvalidArgument("set-time cannot be empty")
        parse_text = f"{text[:-1]}+00:00" if text.endswith("Z") else text
        try:
            datetime.datetime.fromisoformat(parse_text)
        except ValueError as exc:
            raise InvalidArgument(
                f"set-time must be an ISO-8601 DateTime: {text}"
            ) from exc
        return text

    @staticmethod
    def _payload(set_time):
        """Build a DellTimeService.ManageTime payload.

        :param set_time: DateTime string to set, or None to query.
        :return: tuple of (payload, level).
        """
        if set_time is None:
            return {"GetRequest": True}, "read_only"
        return {
            "GetRequest": False,
            "TimeData": DellTimeManage._validate_time_data(set_time),
        }, "destructive"

    def _preview(self, rows, payload, level, blocked):
        """Return a no-POST command preview.

        :param rows: selected target rows.
        :param payload: payload that would be posted.
        :param level: risk level for the selected mode.
        :param blocked: optional reason the POST is blocked.
        :return: preview CommandResult.
        """
        return CommandResult({
            "dry_run": True,
            "action": _DELL_TIME_ACTION,
            "targets": rows,
            "payload": payload,
            "level": level,
            "blocked": blocked,
        }, None, None, None)

    def execute(self,
                manager: Optional[str] = None,
                set_time: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Query or set Dell service-processor time.

        :param manager: optional manager Id or URI selector.
        :param set_time: ISO-8601 DateTime to set; None queries current time.
        :param confirm: allow a set-time POST to be sent.
        :param dry_run: resolve target and payload without POSTing.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue underlying reads/POSTs over the async path when True.
        :return: CommandResult containing preview data or POST results.
        """
        rows = self._select_rows(self._discover_rows(do_async), manager)
        if not rows:
            return CommandResult(
                {"action": _DELL_TIME_ACTION, "available": []},
                None,
                None,
                "DellTimeService.ManageTime action not found",
            )
        if set_time is not None and manager is None and len(rows) > 1:
            managers = [row["Manager"] for row in rows]
            raise InvalidArgument(
                f"--manager is required when setting time on multiple managers: "
                f"{managers}"
            )

        payload, level = self._payload(set_time)
        if dry_run:
            return self._preview(rows, payload, level, None)
        if set_time is not None and not confirm:
            return self._preview(
                rows,
                payload,
                level,
                "DellTimeService.ManageTime set requires --confirm",
            )

        results = []
        first_error = None
        for row in rows:
            result, status = self.base_post(
                row["Target"],
                payload=payload,
                do_async=do_async,
                expected_status=200,
            )
            entry = dict(row)
            entry["status"] = str(status)
            entry["response"] = result.data
            if result.error:
                entry["error"] = result.error
                first_error = first_error or result.error
            results.append(entry)

        data = {
            "action": _DELL_TIME_ACTION,
            "payload": payload,
            "level": level,
            "executed": True,
            "results": results,
        }
        return CommandResult(data, None, None, first_error)
