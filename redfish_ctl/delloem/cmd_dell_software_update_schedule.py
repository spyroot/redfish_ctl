"""Manage Dell SoftwareInstallationService update schedules.

    redfish_ctl dell-software-update-schedule
    redfish_ctl dell-software-update-schedule --action set --payload-json '{"ShareType":"HTTP"}'
    redfish_ctl dell-software-update-schedule --action clear --confirm

The Dell OEM ``DellSoftwareInstallationService`` advertises schedule mutation
actions in the ComputerSystem OEM links. This command discovers those targets,
lists them by default, and previews schedule set/clear payloads unless
``--confirm`` is supplied.

Author Mus spyroot@gmail.com
"""
import json
from abc import abstractmethod
from dataclasses import dataclass
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..redfish_manager import CommandResult
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_shared import RedfishApi

_SERVICE_FALLBACK = (
    "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/"
    "DellSoftwareInstallationService"
)
_REDACTED = "********"


@dataclass(frozen=True)
class _DellSoftwareScheduleSpec:
    """Selector metadata for one Dell software update schedule action."""

    selector: str
    full_type: str
    action_name: str
    description: str


_ACTION_SPECS = {
    "set": _DellSoftwareScheduleSpec(
        selector="set",
        full_type="#DellSoftwareInstallationService.SetUpdateSchedule",
        action_name="SetUpdateSchedule",
        description="set the Dell software update schedule",
    ),
    "clear": _DellSoftwareScheduleSpec(
        selector="clear",
        full_type="#DellSoftwareInstallationService.ClearUpdateSchedule",
        action_name="ClearUpdateSchedule",
        description="clear the Dell software update schedule",
    ),
}


class DellSoftwareUpdateSchedule(
    IDracManager,
    scm_type=ApiRequestType.DellSoftwareUpdateSchedule,
    name="dell-software-update-schedule",
    metaclass=Singleton,
):
    """Discover and invoke Dell software update schedule actions."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-software-update-schedule command."""
        super(DellSoftwareUpdateSchedule, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``dell-software-update-schedule`` subcommand.

        :param cls: command class used to build the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--action",
            choices=sorted(_ACTION_SPECS),
            default=None,
            help="schedule action to run; omit to list available targets",
        )
        cmd_parser.add_argument(
            "--software-service-uri",
            dest="software_service_uri",
            type=str,
            default=None,
            help="specific DellSoftwareInstallationService URI when discovery needs override",
        )
        cmd_parser.add_argument(
            "--payload-json",
            dest="payload_json",
            type=str,
            default=None,
            help="JSON object payload for SetUpdateSchedule vendor fields",
        )
        cmd_parser.add_argument(
            "--share-type",
            dest="share_type",
            type=str,
            default=None,
            help="SetUpdateSchedule ShareType value advertised by the BMC",
        )
        cmd_parser.add_argument(
            "--apply-reboot",
            dest="apply_reboot",
            type=str,
            default=None,
            help="SetUpdateSchedule ApplyReboot value, such as NoReboot",
        )
        cmd_parser.add_argument(
            "--ignore-cert-warning",
            dest="ignore_cert_warning",
            type=str,
            default=None,
            help="SetUpdateSchedule IgnoreCertWarning value, such as Off or On",
        )
        cmd_parser.add_argument(
            "--proxy-support",
            dest="proxy_support",
            type=str,
            default=None,
            help="SetUpdateSchedule ProxySupport value advertised by the BMC",
        )
        cmd_parser.add_argument(
            "--proxy-type",
            dest="proxy_type",
            type=str,
            default=None,
            help="SetUpdateSchedule ProxyType value advertised by the BMC",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            dest="confirm",
            default=False,
            help="fire the schedule action POST; without it the command previews",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="resolve the target without POSTing; overrides --confirm",
        )
        return (
            cmd_parser,
            "dell-software-update-schedule",
            "command manage Dell software update schedules",
        )

    @staticmethod
    def _link(data, key):
        """Return an ``@odata.id`` link value from a Redfish object.

        :param data: Redfish resource body.
        :param key: link property name.
        :return: linked URI, or None when absent or malformed.
        """
        link = data.get(key) if isinstance(data, dict) else None
        return link.get("@odata.id") if isinstance(link, dict) else None

    @staticmethod
    def _dell(data):
        """Return the ``Oem.Dell`` extension block from a resource.

        :param data: Redfish resource body.
        :return: Dell OEM block, or an empty dict.
        """
        oem = data.get("Oem") if isinstance(data, dict) else None
        dell = oem.get("Dell") if isinstance(oem, dict) else None
        return dell if isinstance(dell, dict) else {}

    @staticmethod
    def _clean(value):
        """Strip optional string values and omit blank strings.

        :param value: candidate payload value.
        :return: stripped string, original value, or None when blank.
        """
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @staticmethod
    def _payload_from_json(payload_json):
        """Parse a JSON object payload.

        :param payload_json: JSON object text, or None.
        :return: parsed payload dict.
        :raises InvalidArgument: when the JSON is invalid or not an object.
        """
        if payload_json is None:
            return {}
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError as exc:
            raise InvalidArgument(f"invalid --payload-json: {exc}") from exc
        if not isinstance(payload, dict):
            raise InvalidArgument("--payload-json must be a JSON object")
        return dict(payload)

    @staticmethod
    def _redact_payload(payload):
        """Return a copy of a payload with password-like fields masked.

        :param payload: action payload dict.
        :return: redacted payload copy.
        """
        redacted = {}
        for key, value in payload.items():
            if "password" in key.lower():
                redacted[key] = _REDACTED
            else:
                redacted[key] = value
        return redacted

    @classmethod
    def _payload(cls,
                 payload_json=None,
                 share_type=None,
                 apply_reboot=None,
                 ignore_cert_warning=None,
                 proxy_support=None,
                 proxy_type=None):
        """Build a SetUpdateSchedule payload from JSON plus typed overrides.

        :param payload_json: JSON object text with vendor-specific schedule fields.
        :param share_type: optional ShareType value.
        :param apply_reboot: optional ApplyReboot value.
        :param ignore_cert_warning: optional IgnoreCertWarning value.
        :param proxy_support: optional ProxySupport value.
        :param proxy_type: optional ProxyType value.
        :return: JSON-serializable payload dict.
        """
        payload = cls._payload_from_json(payload_json)
        updates = {
            "ShareType": cls._clean(share_type),
            "ApplyReboot": cls._clean(apply_reboot),
            "IgnoreCertWarning": cls._clean(ignore_cert_warning),
            "ProxySupport": cls._clean(proxy_support),
            "ProxyType": cls._clean(proxy_type),
        }
        payload.update({key: value for key, value in updates.items()
                        if value is not None})
        return payload

    def _get(self, uri, do_async):
        """GET a Redfish resource body, treating optional misses as absent.

        :param uri: Redfish resource URI.
        :param do_async: run the query asynchronously when True.
        :return: parsed resource body, or an empty dict when the read fails.
        """
        try:
            data = self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _system_uris(self, do_async):
        """Return ComputerSystem member URIs.

        :param do_async: run the query asynchronously when True.
        :return: list of system resource URIs.
        """
        root = self._get(RedfishApi.Version, do_async)
        systems_uri = self._link(root, "Systems") or f"{RedfishApi.Version}/Systems"
        systems = self._get(systems_uri, do_async)
        members = systems.get("Members") if isinstance(systems, dict) else []
        return [
            member["@odata.id"]
            for member in members
            if isinstance(member, dict) and isinstance(member.get("@odata.id"), str)
        ]

    def _service_uris(self, do_async, service_uri=None):
        """Discover DellSoftwareInstallationService resource URIs.

        :param do_async: run the query asynchronously when True.
        :param service_uri: optional caller-supplied service URI.
        :return: ordered list of discovered service URIs.
        """
        if service_uri:
            return [service_uri]

        uris = []
        for system_uri in self._system_uris(do_async):
            system = self._get(system_uri, do_async)
            discovered = self._link(
                self._dell(system),
                "DellSoftwareInstallationService",
            )
            if discovered and discovered not in uris:
                uris.append(discovered)
        if not uris:
            uris.append(_SERVICE_FALLBACK)
        return uris

    def _discover_rows(self, do_async, service_uri=None):
        """Discover available Dell software update schedule actions.

        :param do_async: run underlying GETs asynchronously when True.
        :param service_uri: optional caller-supplied service URI.
        :return: list of available schedule-action rows.
        """
        rows = []
        for candidate_uri in self._service_uris(do_async, service_uri):
            service = self._get(candidate_uri, do_async)
            actions = self.discover_redfish_actions(self, service)
            targets = self._flatten_action_targets(service)
            for spec in _ACTION_SPECS.values():
                target = targets.get(spec.full_type)
                if not target:
                    continue
                action = actions.get(spec.action_name)
                rows.append({
                    "Action": spec.selector,
                    "FullType": spec.full_type,
                    "Resource": candidate_uri,
                    "Target": target,
                    "Description": spec.description,
                    "AllowableValues": getattr(action, "args", None) or {},
                })
        return rows

    def execute(self,
                action: Optional[str] = None,
                software_service_uri: Optional[str] = None,
                payload_json: Optional[str] = None,
                share_type: Optional[str] = None,
                apply_reboot: Optional[str] = None,
                ignore_cert_warning: Optional[str] = None,
                proxy_support: Optional[str] = None,
                proxy_type: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List, preview, or run Dell software update schedule actions.

        :param action: selector from ``_ACTION_SPECS``; omit to list targets.
        :param software_service_uri: optional direct service resource URI.
        :param payload_json: JSON object payload for SetUpdateSchedule.
        :param share_type: optional ShareType override.
        :param apply_reboot: optional ApplyReboot override.
        :param ignore_cert_warning: optional IgnoreCertWarning override.
        :param proxy_support: optional ProxySupport override.
        :param proxy_type: optional ProxyType override.
        :param confirm: authorize the schedule action POST to actually fire.
        :param dry_run: resolve the target and show the payload without POSTing.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue underlying queries/POST on the async path when True.
        :return: CommandResult with a listing, preview, execution result, or error.
        :raises InvalidArgument: when ``--action set`` has no payload fields.
        """
        rows = self._discover_rows(bool(do_async), software_service_uri)
        if action is None:
            return CommandResult(rows, None, None, None)

        matches = [row for row in rows if row["Action"] == action]
        if not matches:
            return CommandResult(
                {"available": rows},
                None,
                None,
                f"Dell software update schedule action not found: {action}",
            )
        if len(matches) > 1:
            return CommandResult(
                {"matches": matches},
                None,
                None,
                f"multiple Dell software update schedule targets found: {action}",
            )

        spec = _ACTION_SPECS[action]
        payload = {}
        if action == "set":
            payload = self._payload(
                payload_json=payload_json,
                share_type=share_type,
                apply_reboot=apply_reboot,
                ignore_cert_warning=ignore_cert_warning,
                proxy_support=proxy_support,
                proxy_type=proxy_type,
            )
            if not payload:
                raise InvalidArgument(
                    "--action set requires --payload-json or a schedule option"
                )

        result = self.invoke_action(
            matches[0]["Resource"],
            spec.action_name,
            payload=payload,
            full_action_type=spec.full_type,
            do_async=do_async,
            expected_status=202,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
        )
        if isinstance(result.data, dict) and "payload" in result.data:
            result.data["payload"] = self._redact_payload(result.data["payload"])
        return result
