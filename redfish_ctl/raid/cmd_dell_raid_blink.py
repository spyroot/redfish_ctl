"""Blink or stop blinking a Dell RAID physical-disk identify LED.

    redfish_ctl dell-raid-blink
    redfish_ctl dell-raid-blink --target-fqdd Disk.Bay.0
    redfish_ctl dell-raid-blink --operation unblink --target-fqdd Disk.Bay.0 --confirm

The command resolves Dell's ``DellRaidService`` from discovered ComputerSystem
OEM links. Without ``--target-fqdd`` it lists the supported action targets and
candidate drive FQDDs. With ``--target-fqdd`` it previews the request unless
``--confirm`` is set.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton
from ..redfish_shared import RedfishApi

_BLINK_ACTION = "#DellRaidService.BlinkTarget"
_UNBLINK_ACTION = "#DellRaidService.UnBlinkTarget"
_ACTION_NAMES = {
    "blink": "BlinkTarget",
    "unblink": "UnBlinkTarget",
}
_ACTION_TYPES = {
    "blink": _BLINK_ACTION,
    "unblink": _UNBLINK_ACTION,
}
_SYSTEM_FALLBACK = f"{RedfishApi.Version}/Systems/System.Embedded.1"
_SERVICE_SUFFIX = "Oem/Dell/DellRaidService"
_SERVICE_FALLBACK = f"{_SYSTEM_FALLBACK}/{_SERVICE_SUFFIX}"


class DellRaidBlink(
    RedfishManagerBase,
    scm_type=ApiRequestType.DellRaidBlink,
    name="dell-raid-blink",
    metaclass=Singleton,
):
    """Blink or unblink a Dell RAID physical-disk identify LED."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-raid-blink command."""
        super(DellRaidBlink, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``dell-raid-blink`` subcommand.

        :param cls: command class used to build the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--operation",
            choices=sorted(_ACTION_NAMES),
            default="blink",
            help="identify LED action to preview or run",
        )
        cmd_parser.add_argument(
            "--target-fqdd",
            dest="target_fqdd",
            default=None,
            help="Dell physical-disk FQDD, such as Disk.Bay.0",
        )
        cmd_parser.add_argument(
            "--service-uri",
            dest="service_uri",
            default=None,
            help="specific DellRaidService URI when more than one exists",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            default=False,
            help="POST the blink/unblink action instead of previewing it",
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
            "dell-raid-blink",
            "command blink or unblink a Dell RAID physical disk",
        )

    @staticmethod
    def _link(data, key):
        """Return an ``@odata.id`` link value from a Redfish object.

        :param data: Redfish object that may contain a link property.
        :param key: property name whose ``@odata.id`` should be returned.
        :return: linked Redfish URI, or None when absent or malformed.
        """
        link = data.get(key) if isinstance(data, dict) else None
        return link.get("@odata.id") if isinstance(link, dict) else None

    @staticmethod
    def _links_oem_dell(data):
        """Return the ``Links.Oem.Dell`` block from a Redfish object.

        :param data: Redfish object that may contain Dell OEM links.
        :return: Dell OEM links block, or an empty dict when absent.
        """
        links = data.get("Links") if isinstance(data, dict) else None
        oem = links.get("Oem") if isinstance(links, dict) else None
        dell = oem.get("Dell") if isinstance(oem, dict) else None
        return dell if isinstance(dell, dict) else {}

    @staticmethod
    def _member_uris(data, key="Members"):
        """Return member ``@odata.id`` values from a collection-like field.

        :param data: Redfish object carrying a list field.
        :param key: field name containing link objects.
        :return: list of linked Redfish URIs.
        """
        values = data.get(key) if isinstance(data, dict) else None
        if not isinstance(values, list):
            return []
        uris = []
        for value in values:
            uri = value.get("@odata.id") if isinstance(value, dict) else None
            if uri:
                uris.append(uri)
        return uris

    def _get(self, uri, do_async, optional=False):
        """GET a Redfish resource body.

        :param uri: Redfish resource URI.
        :param do_async: issue the query over the async path when True.
        :param optional: return an empty object instead of failing on read errors.
        :return: parsed resource body.
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

    def _system_uris(self):
        """Return discovered ComputerSystem URIs with the Dell fallback last.

        :return: de-duplicated ComputerSystem URI candidates.
        """
        try:
            system_uris = list(self.discover_computer_system_ids() or [])
        except Exception:
            system_uris = []
        system_uris.append(_SYSTEM_FALLBACK)
        seen = set()
        ordered = []
        for uri in system_uris:
            if uri and uri not in seen:
                seen.add(uri)
                ordered.append(uri)
        return ordered

    def _drive_rows(self, system_uri, system, do_async):
        """Return candidate drive FQDDs from a ComputerSystem storage tree.

        :param system_uri: ComputerSystem URI.
        :param system: ComputerSystem resource body.
        :param do_async: issue supporting reads over the async path when True.
        :return: compact drive rows with FQDD, URI, and optional name.
        """
        storage_uri = self._link(system, "Storage") or f"{system_uri}/Storage"
        storage_collection = self._get(storage_uri, do_async, optional=True)
        drives = []
        for storage_member in self._member_uris(storage_collection):
            storage = self._get(storage_member, do_async, optional=True)
            for drive_uri in self._member_uris(storage, key="Drives"):
                drive = self._get(drive_uri, do_async, optional=True)
                fqdd = drive.get("Id") if isinstance(drive, dict) else None
                if not fqdd:
                    fqdd = drive_uri.rstrip("/").rsplit("/", 1)[-1]
                row = {
                    "FQDD": fqdd,
                    "Uri": drive.get("@odata.id", drive_uri)
                    if isinstance(drive, dict)
                    else drive_uri,
                }
                name = drive.get("Name") if isinstance(drive, dict) else None
                if name:
                    row["Name"] = name
                drives.append(row)
        return drives

    def _service_candidates(self, do_async):
        """Return DellRaidService URI candidates.

        :param do_async: issue supporting reads over the async path when True.
        :return: list of dictionaries with system, service, and drive metadata.
        """
        candidates = []
        for system_uri in self._system_uris():
            system_uri = system_uri.rstrip("/")
            system = self._get(system_uri, do_async, optional=True)
            linked = self._link(self._links_oem_dell(system), "DellRaidService")
            service_uri = linked or f"{system_uri}/{_SERVICE_SUFFIX}"
            candidates.append({
                "System": system_uri.rsplit("/", 1)[-1],
                "SystemUri": system_uri,
                "Service": service_uri,
                "Drives": self._drive_rows(system_uri, system, do_async),
            })
        candidates.append({
            "System": "System.Embedded.1",
            "SystemUri": _SYSTEM_FALLBACK,
            "Service": _SERVICE_FALLBACK,
            "Drives": [],
        })

        seen = set()
        ordered = []
        for candidate in candidates:
            service_uri = candidate["Service"]
            if service_uri not in seen:
                seen.add(service_uri)
                ordered.append(candidate)
        return ordered

    def _discover_rows(self, do_async):
        """Discover Dell RAID blink action targets.

        :param do_async: issue discovery queries over the async path when True.
        :return: list of discovered service rows.
        """
        rows = []
        for candidate in self._service_candidates(do_async):
            service = self._get(candidate["Service"], do_async, optional=True)
            if not service:
                continue
            targets = self._flatten_action_targets(service)
            actions = []
            for operation in ("blink", "unblink"):
                target = targets.get(_ACTION_TYPES[operation])
                if target:
                    actions.append({
                        "Operation": operation,
                        "Action": _ACTION_TYPES[operation],
                        "Target": target,
                    })
            if actions:
                rows.append({
                    "System": candidate["System"],
                    "SystemUri": candidate["SystemUri"],
                    "Service": candidate["Service"],
                    "Actions": actions,
                    "Drives": candidate["Drives"],
                })
        return rows

    @staticmethod
    def _select_rows(rows, service_uri):
        """Filter discovered rows by optional service URI.

        :param rows: discovered DellRaidService rows.
        :param service_uri: optional DellRaidService URI selector.
        :return: matching rows.
        :raises InvalidArgument: when the selector matches no discovered row.
        """
        if not service_uri:
            return rows
        normalized = service_uri.rstrip("/")
        matches = [
            row for row in rows
            if row["Service"].rstrip("/") == normalized
        ]
        if not matches:
            available = [row["Service"] for row in rows]
            raise InvalidArgument(
                f"no DellRaidService target for '{service_uri}'; "
                f"available: {available}"
            )
        return matches

    @staticmethod
    def _action_target(row, operation):
        """Return the action target URI for an operation in one service row.

        :param row: discovered DellRaidService row.
        :param operation: ``blink`` or ``unblink``.
        :return: target URI or None.
        """
        action_type = _ACTION_TYPES[operation]
        for action in row["Actions"]:
            if action["Action"] == action_type:
                return action["Target"]
        return None

    def execute(
        self,
        operation: Optional[str] = "blink",
        target_fqdd: Optional[str] = None,
        service_uri: Optional[str] = None,
        confirm: Optional[bool] = False,
        dry_run: Optional[bool] = False,
        filename: Optional[str] = None,
        data_type: Optional[str] = "json",
        verbose: Optional[bool] = False,
        do_async: Optional[bool] = False,
        **kwargs,
    ) -> CommandResult:
        """List, preview, or invoke Dell RAID physical-disk identify LED actions.

        :param operation: ``blink`` or ``unblink``.
        :param target_fqdd: Dell physical-disk FQDD.
        :param service_uri: optional DellRaidService URI selector.
        :param confirm: send the POST when True.
        :param dry_run: resolve the target and payload without POSTing.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue underlying reads/POST on the async path.
        :return: CommandResult with listing, preview, execution, or error.
        """
        operation = operation or "blink"
        if operation not in _ACTION_NAMES:
            raise InvalidArgument(
                f"unsupported Dell RAID blink operation '{operation}'"
            )

        rows = self._select_rows(self._discover_rows(bool(do_async)), service_uri)
        if not rows:
            return CommandResult(
                {"actions": [_BLINK_ACTION, _UNBLINK_ACTION], "available": []},
                None,
                None,
                "DellRaidService BlinkTarget/UnBlinkTarget actions not found",
            )

        target_fqdd = str(target_fqdd).strip() if target_fqdd else None
        if not target_fqdd:
            if confirm or dry_run:
                return CommandResult(
                    {"matches": rows},
                    None,
                    None,
                    "TargetFQDD is required; rerun with --target-fqdd",
                )
            return CommandResult(rows, None, None, None)

        if len(rows) > 1:
            services = [row["Service"] for row in rows]
            return CommandResult(
                {"matches": rows},
                None,
                None,
                "multiple DellRaidService targets found; pass --service-uri "
                f"with one of: {services}",
            )

        row = rows[0]
        action_target = self._action_target(row, operation)
        if not action_target:
            return CommandResult(
                {"operation": operation, "service": row["Service"]},
                None,
                None,
                f"DellRaidService.{_ACTION_NAMES[operation]} action not found",
            )

        result = self.invoke_action(
            row["Service"],
            _ACTION_NAMES[operation],
            payload={"TargetFQDD": target_fqdd},
            full_action_type=_ACTION_TYPES[operation],
            do_async=do_async,
            dry_run=bool(dry_run) or not bool(confirm),
            confirm=bool(confirm),
        )
        if isinstance(result.data, dict):
            result.data["operation"] = operation
            result.data["service"] = row["Service"]
            result.data["target_fqdd"] = target_fqdd
        return result
