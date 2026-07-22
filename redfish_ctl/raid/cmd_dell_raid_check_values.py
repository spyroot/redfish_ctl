"""Validate proposed DellRaidService virtual-disk property values.

    redfish_ctl dell-raid-check-values
    redfish_ctl dell-raid-check-values --property RAIDLevel --value RAID1
    redfish_ctl dell-raid-check-values --property Size --value 1024 --dry_run

Dell exposes ``#DellRaidService.CheckVDValues`` as a POST-backed validation
action on ``DellRaidService``. The action checks proposed virtual-disk values
and returns validation data; it does not change storage state.
"""
import json
from abc import abstractmethod
from typing import Iterable, Optional

from ..cmd_exceptions import InvalidArgument
from ..redfish_manager import CommandResult
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, RedfishApiRespond, Singleton
from ..redfish_shared import RedfishApi

_CHECK_ACTION = "#DellRaidService.CheckVDValues"
_CHECK_NAME = "CheckVDValues"


def _as_list(values: Optional[Iterable[str]]) -> list[str]:
    """Normalize argparse and direct-call values to a list of strings.

    :param values: None, one string, or an iterable of strings.
    :return: normalized list, empty when no values were supplied.
    """
    if values is None:
        return []
    if isinstance(values, str):
        return [values]
    return [str(value) for value in values]


class DellRaidCheckValues(IDracManager,
                          scm_type=ApiRequestType.DellRaidCheckValues,
                          name="dell-raid-check-values",
                          metaclass=Singleton):
    """Run DellRaidService.CheckVDValues as a read-only validation query."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-raid-check-values command."""
        super(DellRaidCheckValues, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``dell-raid-check-values`` subcommand.

        :param cls: command class used to build the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--property",
            action="append",
            dest="property_names",
            default=None,
            help="virtual-disk property name to validate; repeat with --value",
        )
        cmd_parser.add_argument(
            "--value",
            action="append",
            dest="property_values",
            default=None,
            help="virtual-disk property value paired with --property",
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
            "dell-raid-check-values",
            "command validate Dell RAID virtual-disk values",
        )

    @staticmethod
    def _link(data, key):
        """Return an ``@odata.id`` link from a Redfish object.

        :param data: Redfish resource body.
        :param key: link property name.
        :return: linked URI, or None when absent or malformed.
        """
        link = data.get(key) if isinstance(data, dict) else None
        return link.get("@odata.id") if isinstance(link, dict) else None

    @staticmethod
    def _dell_links(resource):
        """Return the ``Links.Oem.Dell`` block from a ComputerSystem.

        :param resource: Redfish ComputerSystem resource body.
        :return: Dell OEM links block, or an empty dict.
        """
        links = resource.get("Links") if isinstance(resource, dict) else None
        oem = links.get("Oem") if isinstance(links, dict) else None
        dell = oem.get("Dell") if isinstance(oem, dict) else None
        return dell if isinstance(dell, dict) else {}

    def _get(self, uri, do_async):
        """GET a Redfish resource, returning an empty dict on optional gaps.

        :param uri: Redfish URI to read.
        :param do_async: issue the query on the async path when True.
        :return: parsed JSON dict, or an empty dict.
        """
        try:
            data = self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _raid_service_uri(self, do_async):
        """Resolve DellRaidService from the selected ComputerSystem.

        :param do_async: issue the system read on the async path when True.
        :return: DellRaidService URI, falling back to the legacy Dell path.
        """
        system_uri = self.idrac_manage_servers
        system = self._get(system_uri, do_async)
        linked = self._link(self._dell_links(system), "DellRaidService")
        if linked:
            return linked
        system_id = system_uri.rstrip("/").rsplit("/", 1)[-1]
        return f"{RedfishApi.Version}/Dell/Systems/{system_id}/DellRaidService"

    @staticmethod
    def _allowable(action):
        """Return the advertised virtual-disk property names for CheckVDValues.

        :param action: discovered RedfishAction for ``CheckVDValues``.
        :return: sorted allowed ``VDPropNameArrayIn`` entries.
        """
        args = getattr(action, "args", {}) or {}
        return sorted(args.get("VDPropNameArrayIn", []) or [])

    def _discover_row(self, do_async):
        """Discover the CheckVDValues target and advertised property names.

        :param do_async: issue underlying Redfish reads on the async path when True.
        :return: tuple of (service URI, action map, row dict or None).
        """
        service_uri = self._raid_service_uri(do_async)
        service = self._get(service_uri, do_async)
        actions = self.discover_redfish_actions(self, service)
        targets = self._flatten_action_targets(service)
        target = targets.get(_CHECK_ACTION)
        if not target:
            return service_uri, actions, None
        action = actions.get(_CHECK_NAME)
        return service_uri, actions, {
            "Action": "check-vd-values",
            "FullType": _CHECK_ACTION,
            "Resource": service_uri,
            "Target": target,
            "Description": "validate proposed Dell RAID virtual-disk values",
            "Parameters": {
                "VDPropNameArrayIn": self._allowable(action),
                "VDPropValueArrayIn": [],
            },
        }

    @staticmethod
    def _payload(property_names, property_values):
        """Build and validate a CheckVDValues payload.

        :param property_names: selected virtual-disk property names.
        :param property_values: selected virtual-disk property values.
        :return: CheckVDValues payload, or None when the caller requested a list.
        :raises InvalidArgument: if names and values are missing or mismatched.
        """
        names = _as_list(property_names)
        values = _as_list(property_values)
        if not names and not values:
            return None
        if not names or not values:
            raise InvalidArgument("provide both --property and --value")
        if len(names) != len(values):
            raise InvalidArgument("--property and --value must be supplied in pairs")
        return {
            "VDPropNameArrayIn": names,
            "VDPropValueArrayIn": values,
        }

    def _invoke_sync(self, preview, payload):
        """POST the read-only validation action and preserve the JSON response.

        :param preview: dry-run CommandResult from ``invoke_action``.
        :param payload: CheckVDValues payload to send.
        :return: CommandResult with execution metadata and response body.
        """
        if not isinstance(preview.data, dict):
            return preview
        if preview.data.get("level") != "read_only":
            return CommandResult(
                preview.data,
                preview.discovered,
                None,
                f"{_CHECK_ACTION} is not classified as read-only",
            )

        target = preview.data["target"]
        response = self.api_post_call(
            f"{self._default_method}{self.redfish_ip}{target}",
            json.dumps(payload),
            self.json_content_type,
        )
        api_resp = self.default_post_success(response, expected=200)
        data = {
            "executed": True,
            "action": _CHECK_ACTION,
            "target": target,
            "payload": payload,
            "level": "read_only",
        }
        data.update(self.api_success_msg(api_resp))
        if api_resp == RedfishApiRespond.AcceptedTaskGenerated:
            data["task_id"] = self.job_id_from_header(response)
        else:
            try:
                data["response"] = response.json()
            except ValueError:
                pass
        return CommandResult(data, preview.discovered, None, None)

    def execute(self,
                property_names: Optional[Iterable[str]] = None,
                property_values: Optional[Iterable[str]] = None,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List or run the DellRaidService.CheckVDValues action.

        :param property_names: repeated virtual-disk property names.
        :param property_values: repeated virtual-disk property values.
        :param dry_run: resolve and validate without POSTing.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue the underlying reads/POST on the async path when True.
        :return: CommandResult with a listing, dry-run preview, execution result,
            or missing-action error.
        """
        service_uri, actions, row = self._discover_row(bool(do_async))
        payload = self._payload(property_names, property_values)
        if payload is None:
            return CommandResult([row] if row else [], actions, None, None)
        if row is None:
            return CommandResult(
                {
                    "raid_service": service_uri,
                    "action": _CHECK_ACTION,
                    "available": sorted(actions),
                },
                actions,
                None,
                "Dell RAID CheckVDValues action not found",
            )

        preview = self.invoke_action(
            row["Resource"],
            _CHECK_NAME,
            payload=payload,
            full_action_type=_CHECK_ACTION,
            do_async=bool(do_async),
            expected_status=200,
            dry_run=True,
        )
        if preview.error or dry_run:
            return preview
        if do_async:
            return self.invoke_action(
                row["Resource"],
                _CHECK_NAME,
                payload=payload,
                full_action_type=_CHECK_ACTION,
                do_async=True,
                expected_status=200,
            )
        return self._invoke_sync(preview, payload)
