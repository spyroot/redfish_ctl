"""Preview or invoke Dell BIOS device recovery.

    redfish_ctl dell-bios-device-recovery
    redfish_ctl dell-bios-device-recovery --system-uri /redfish/v1/Systems/System.Embedded.1
    redfish_ctl dell-bios-device-recovery --device BIOS --confirm

The command resolves ``#DellBIOSService.DeviceRecovery`` from a Dell
ComputerSystem's OEM DellBIOSService link. The action is classified as
destructive, so the default invocation only previews the target and payload.
"""
from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult

_DEVICE_RECOVERY_ACTION = "#DellBIOSService.DeviceRecovery"
_DELL_BIOS_SERVICE = "Oem/Dell/DellBIOSService"


class DellBiosDeviceRecovery(IDracManager,
                             scm_type=ApiRequestType.DellBiosDeviceRecovery,
                             name="dell-bios-device-recovery",
                             metaclass=Singleton):
    """Preview or invoke DellBIOSService.DeviceRecovery."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-bios-device-recovery command."""
        super(DellBiosDeviceRecovery, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded ``dell-bios-device-recovery`` subcommand.

        :param cls: command class supplying the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--system-uri",
            required=False,
            dest="system_uri",
            type=str,
            default=None,
            help="ComputerSystem URI that owns DellBIOSService; omitted value "
                 "probes discovered systems",
        )
        cmd_parser.add_argument(
            "--device",
            required=False,
            dest="device",
            type=str,
            default="BIOS",
            help="DeviceRecovery Device payload value advertised by the service",
        )
        cmd_parser.add_argument(
            "--list",
            action="store_true",
            dest="list_only",
            default=False,
            help="list discovered DellBIOSService targets without POSTing",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            dest="confirm",
            default=False,
            help="fire the DeviceRecovery POST; without it the command previews",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="resolve the target and show it without POSTing; overrides --confirm",
        )
        return (
            cmd_parser,
            "dell-bios-device-recovery",
            "command recover a Dell BIOS device through DellBIOSService",
        )

    @staticmethod
    def _normalize_uri(uri):
        """Return a Redfish URI without a trailing slash.

        :param uri: Redfish resource URI.
        :return: normalized URI, or None for blank input.
        """
        if uri is None:
            return None
        normalized = str(uri).strip().rstrip("/")
        return normalized or None

    @staticmethod
    def _link(data, key):
        """Return a Redfish ``@odata.id`` link from ``data[key]``.

        :param data: resource object containing a link field.
        :param key: link key to read.
        :return: linked URI, or None when absent or malformed.
        """
        link = data.get(key) if isinstance(data, dict) else None
        return link.get("@odata.id") if isinstance(link, dict) else None

    @classmethod
    def _oem_dell(cls, system):
        """Return a ComputerSystem ``Links.Oem.Dell`` block.

        :param system: ComputerSystem resource body.
        :return: Dell OEM links object, or an empty dict.
        """
        links = system.get("Links") if isinstance(system, dict) else None
        oem = links.get("Oem") if isinstance(links, dict) else None
        dell = oem.get("Dell") if isinstance(oem, dict) else None
        return dell if isinstance(dell, dict) else {}

    def _get_object(self, uri, do_async, optional=False):
        """Read a Redfish object resource.

        :param uri: Redfish resource URI.
        :param do_async: issue the GET over the async path when True.
        :param optional: return an empty object instead of surfacing read errors.
        :return: parsed object body or an empty dict.
        """
        try:
            data = self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            if optional:
                return {}
            raise
        return data if isinstance(data, dict) else {}

    def _candidate_systems(self, system_uri=None):
        """Return ComputerSystem URIs that may own DellBIOSService.

        :param system_uri: optional explicit ComputerSystem URI.
        :return: de-duplicated list of normalized ComputerSystem URIs.
        """
        explicit = self._normalize_uri(system_uri)
        if explicit is not None:
            return [explicit]

        candidates = []
        try:
            candidates.extend(self.discover_computer_system_ids() or [])
        except Exception:
            pass
        try:
            candidates.append(self.idrac_manage_servers)
        except Exception:
            pass

        seen = set()
        normalized = []
        for candidate in candidates:
            value = self._normalize_uri(candidate)
            if value is not None and value not in seen:
                normalized.append(value)
                seen.add(value)
        return normalized

    @classmethod
    def _fallback_service_uri(cls, system_uri):
        """Return the conventional DellBIOSService child URI.

        :param system_uri: ComputerSystem URI.
        :return: DellBIOSService URI under the system.
        """
        return f"{system_uri.rstrip('/')}/{_DELL_BIOS_SERVICE}"

    def _service_uri_for_system(self, system_uri, do_async):
        """Resolve DellBIOSService from a ComputerSystem OEM Dell link.

        :param system_uri: ComputerSystem URI.
        :param do_async: issue the ComputerSystem GET over the async path.
        :return: advertised or conventional DellBIOSService URI.
        """
        system = self._get_object(system_uri, do_async, optional=True)
        linked = self._link(self._oem_dell(system), "DellBIOSService")
        return linked or self._fallback_service_uri(system_uri)

    @staticmethod
    def _allowable_devices(service):
        """Return Device allowable values advertised by DellBIOSService.

        :param service: DellBIOSService resource body.
        :return: list of allowed Device values.
        """
        actions = service.get("Actions") if isinstance(service, dict) else None
        action = actions.get(_DEVICE_RECOVERY_ACTION) if isinstance(actions, dict) else None
        values = (
            action.get("Device@Redfish.AllowableValues")
            if isinstance(action, dict)
            else None
        )
        return list(values) if isinstance(values, list) else []

    def _recovery_targets(self, do_async, system_uri=None):
        """Discover DellBIOSService DeviceRecovery action targets.

        :param do_async: issue Redfish queries on the async path.
        :param system_uri: optional explicit ComputerSystem URI.
        :return: CommandResult containing discovered target rows or an error.
        """
        candidates = self._candidate_systems(system_uri)
        if not candidates:
            return CommandResult(
                {"action": _DEVICE_RECOVERY_ACTION, "targets": []},
                None,
                None,
                "no ComputerSystem URI available for DellBIOSService",
            )

        rows = []
        attempted = []
        last_actions = None
        last_targets = {}
        for candidate in candidates:
            service_uri = self._service_uri_for_system(candidate, do_async)
            attempted.append(service_uri)
            service = self._get_object(service_uri, do_async, optional=True)
            actions = self.discover_redfish_actions(self, service)
            targets = self._flatten_action_targets(service)
            last_actions = actions
            last_targets = targets
            target = targets.get(_DEVICE_RECOVERY_ACTION)
            if target is None:
                continue
            rows.append({
                "system": candidate,
                "bios_service": service_uri,
                "target": target,
                "devices": self._allowable_devices(service),
            })

        if rows:
            return CommandResult(
                {"action": _DEVICE_RECOVERY_ACTION, "targets": rows},
                last_actions,
                None,
                None,
            )

        available = sorted(
            set(list((last_actions or {}).keys()) + list(last_targets.keys()))
        )
        return CommandResult(
            {
                "action": _DEVICE_RECOVERY_ACTION,
                "attempted": attempted,
                "available": available,
            },
            last_actions,
            None,
            (
                f"action '{_DEVICE_RECOVERY_ACTION}' not found on "
                "DellBIOSService"
            ),
        )

    @staticmethod
    def _payload(device):
        """Build the DeviceRecovery payload.

        :param device: advertised Device value to recover.
        :return: DeviceRecovery payload.
        """
        return {"Device": str(device or "").strip()}

    def execute(self,
                system_uri: Optional[str] = None,
                device: Optional[str] = "BIOS",
                list_only: Optional[bool] = False,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Preview or run DellBIOSService.DeviceRecovery.

        :param system_uri: optional ComputerSystem URI owning DellBIOSService.
        :param device: Device payload value; usually ``BIOS``.
        :param list_only: list discovered targets without POSTing.
        :param confirm: authorize the DeviceRecovery POST to actually fire.
        :param dry_run: resolve the target and show it without POSTing.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue underlying queries and POST on the async path.
        :return: CommandResult with target metadata, preview, or POST result.
        """
        metadata = self._recovery_targets(do_async, system_uri=system_uri)
        if metadata.error is not None:
            return metadata

        rows = metadata.data["targets"]
        payload = self._payload(device)
        if list_only:
            return CommandResult(
                {
                    "action": _DEVICE_RECOVERY_ACTION,
                    "targets": rows,
                    "payload": payload,
                },
                metadata.discovered,
                None,
                None,
            )

        if len(rows) > 1 and system_uri is None:
            return CommandResult(
                {
                    "dry_run": True,
                    "action": _DEVICE_RECOVERY_ACTION,
                    "targets": rows,
                    "payload": payload,
                    "blocked": "select --system-uri before invoking DeviceRecovery",
                },
                metadata.discovered,
                None,
                None,
            )

        row = rows[0]
        result = self.invoke_action(
            row["bios_service"],
            "DeviceRecovery",
            payload=payload,
            full_action_type=_DEVICE_RECOVERY_ACTION,
            do_async=do_async,
            expected_status=202,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
        )
        if isinstance(result.data, dict):
            result.data.setdefault("system", row["system"])
            result.data.setdefault("bios_service", row["bios_service"])
            result.data.setdefault("device", payload["Device"])
        return result
