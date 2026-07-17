"""Run read-only DellRaidService query actions.

    redfish_ctl dell-raid-queries
    redfish_ctl dell-raid-queries --query raid-levels
    redfish_ctl dell-raid-queries --query available-disks --disk-type SSD

Dell exposes some inventory-style RAID lookups as Redfish actions on
``DellRaidService``. These POST actions return information instead of changing
storage state, so this command only wires the captured query actions and leaves
mutating RAID operations to their own guarded commands.

Author Mus spyroot@gmail.com
"""
import json
from abc import abstractmethod
from dataclasses import dataclass
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, RedfishApiRespond, Singleton
from ..redfish_shared import RedfishApi


@dataclass(frozen=True)
class _DellRaidQuerySpec:
    """Static selector metadata for one DellRaidService query action."""

    selector: str
    full_type: str
    action_name: str
    description: str
    payload_keys: tuple[str, ...]


_COMMON_FILTERS = (
    "BlockSizeInBytes",
    "DiskEncrypt",
    "DiskType",
    "Diskprotocol",
    "FormFactor",
    "T10PIStatus",
)

_QUERY_SPECS = {
    "available-disks": _DellRaidQuerySpec(
        selector="available-disks",
        full_type="#DellRaidService.GetAvailableDisks",
        action_name="GetAvailableDisks",
        description="return disks matching RAID capability filters",
        payload_keys=_COMMON_FILTERS + ("RaidLevel",),
    ),
    "dhs-disks": _DellRaidQuerySpec(
        selector="dhs-disks",
        full_type="#DellRaidService.GetDHSDisks",
        action_name="GetDHSDisks",
        description="return Dell dedicated-hot-spare disk candidates",
        payload_keys=(),
    ),
    "raid-levels": _DellRaidQuerySpec(
        selector="raid-levels",
        full_type="#DellRaidService.GetRAIDLevels",
        action_name="GetRAIDLevels",
        description="return RAID levels matching disk capability filters",
        payload_keys=_COMMON_FILTERS,
    ),
}

_CLI_PAYLOAD_KEYS = {
    "block_size": "BlockSizeInBytes",
    "disk_encrypt": "DiskEncrypt",
    "disk_type": "DiskType",
    "disk_protocol": "Diskprotocol",
    "form_factor": "FormFactor",
    "raid_level": "RaidLevel",
    "t10_pi_status": "T10PIStatus",
}


class DellRaidQueries(RedfishManagerBase,
                      scm_type=ApiRequestType.DellRaidQueries,
                      name="dell-raid-queries",
                      metaclass=Singleton):
    """Discover and invoke read-only DellRaidService query actions."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-raid-queries command."""
        super(DellRaidQueries, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``dell-raid-queries`` subcommand.

        :param cls: command class used to build the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--query",
            choices=sorted(_QUERY_SPECS),
            default=None,
            help="Dell RAID query action to run; omit to list available targets",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="resolve the target and payload without POSTing",
        )
        cmd_parser.add_argument("--block-size", dest="block_size", default=None)
        cmd_parser.add_argument("--disk-encrypt", dest="disk_encrypt", default=None)
        cmd_parser.add_argument("--disk-type", dest="disk_type", default=None)
        cmd_parser.add_argument("--disk-protocol", dest="disk_protocol", default=None)
        cmd_parser.add_argument("--form-factor", dest="form_factor", default=None)
        cmd_parser.add_argument("--raid-level", dest="raid_level", default=None)
        cmd_parser.add_argument("--t10-pi-status", dest="t10_pi_status", default=None)
        return (
            cmd_parser,
            "dell-raid-queries",
            "command run read-only Dell RAID service query actions",
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
    def _dell_links(resource):
        """Return the ``Links.Oem.Dell`` block from a resource.

        :param resource: Redfish resource body.
        :return: Dell OEM links block, or an empty dict.
        """
        links = resource.get("Links") if isinstance(resource, dict) else None
        oem = links.get("Oem") if isinstance(links, dict) else None
        dell = oem.get("Dell") if isinstance(oem, dict) else None
        return dell if isinstance(dell, dict) else {}

    def _get(self, uri, do_async):
        """GET a Redfish resource body.

        :param uri: Redfish resource URI.
        :param do_async: run the query asynchronously when True.
        :return: parsed resource body, or an empty dict when the read fails.
        """
        try:
            data = self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _system_uri(self):
        """Return the selected ComputerSystem URI.

        :return: discovered ComputerSystem URI.
        """
        return self.idrac_manage_servers

    def _raid_service_uri(self, do_async):
        """Resolve the DellRaidService URI from the ComputerSystem OEM links.

        :param do_async: run the system query asynchronously when True.
        :return: DellRaidService URI.
        """
        system_uri = self._system_uri()
        system = self._get(system_uri, do_async)
        dell_links = self._dell_links(system)
        linked = self._link(dell_links, "DellRaidService")
        if linked:
            return linked
        system_id = system_uri.rstrip("/").rsplit("/", 1)[-1]
        return f"{RedfishApi.Version}/Dell/Systems/{system_id}/DellRaidService"

    @staticmethod
    def _allowable(action, key):
        """Return sorted allowable values advertised for one action parameter.

        :param action: discovered RedfishAction.
        :param key: action payload key.
        :return: sorted allowable values.
        """
        args = getattr(action, "args", {}) or {}
        return sorted(args.get(key, []) or [])

    def _discover_rows(self, do_async):
        """Discover available DellRaidService query actions.

        :param do_async: run underlying queries asynchronously when True.
        :return: tuple of service URI, action map, and available rows.
        """
        service_uri = self._raid_service_uri(do_async)
        service = self._get(service_uri, do_async)
        actions = self.discover_redfish_actions(self, service)
        targets = self._flatten_action_targets(service)
        rows = []
        for spec in _QUERY_SPECS.values():
            target = targets.get(spec.full_type)
            action = actions.get(spec.action_name)
            if not target:
                continue
            rows.append({
                "Action": spec.selector,
                "FullType": spec.full_type,
                "Resource": service_uri,
                "Target": target,
                "Description": spec.description,
                "Parameters": {
                    key: self._allowable(action, key)
                    for key in spec.payload_keys
                },
            })
        return service_uri, actions, rows

    @staticmethod
    def _payload_from_args(spec, values):
        """Build the selected query payload from CLI filter values.

        :param spec: selected query metadata.
        :param values: mapping of CLI option names to values.
        :return: action payload.
        :raises InvalidArgument: when a filter is not supported by the query.
        """
        payload = {}
        unsupported = []
        for cli_key, payload_key in _CLI_PAYLOAD_KEYS.items():
            value = values.get(cli_key)
            if value is None:
                continue
            if payload_key not in spec.payload_keys:
                unsupported.append(payload_key)
                continue
            payload[payload_key] = value
        if unsupported:
            raise InvalidArgument(
                f"{spec.selector} does not accept: {', '.join(sorted(unsupported))}"
            )
        return payload

    def _invoke_query_action(self, row, spec, payload, do_async, dry_run):
        """Invoke a selected read-only query action and preserve JSON bodies.

        :param row: discovered query-action row.
        :param spec: selected query metadata.
        :param payload: action payload.
        :param do_async: use the async Redfish transport when True.
        :param dry_run: resolve and validate without POSTing when True.
        :return: CommandResult for dry-run, validation, task, or JSON response.
        """
        preview = self.invoke_action(
            row["Resource"],
            spec.action_name,
            payload=payload,
            full_action_type=spec.full_type,
            do_async=do_async,
            expected_status=200,
            dry_run=True,
        )
        if preview.error or dry_run:
            return preview
        if not isinstance(preview.data, dict):
            return preview
        if preview.data.get("level") != "read_only":
            return CommandResult(
                preview.data,
                preview.discovered,
                None,
                f"{spec.full_type} is not classified as read-only",
            )
        if do_async:
            return self.invoke_action(
                row["Resource"],
                spec.action_name,
                payload=payload,
                full_action_type=spec.full_type,
                do_async=True,
                expected_status=200,
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
            "action": spec.full_type,
            "target": target,
            "payload": payload,
            "level": "read_only",
        }
        data.update(self.api_success_msg(api_resp))
        if api_resp == RedfishApiRespond.AcceptedTaskGenerated:
            data["task_id"] = self.job_id_from_header(response)
        else:
            try:
                body = response.json()
            except ValueError:
                body = None
            if body is not None:
                data["response"] = body
        return CommandResult(data, preview.discovered, None, None)

    def execute(self,
                query: Optional[str] = None,
                dry_run: Optional[bool] = False,
                block_size: Optional[str] = None,
                disk_encrypt: Optional[str] = None,
                disk_type: Optional[str] = None,
                disk_protocol: Optional[str] = None,
                form_factor: Optional[str] = None,
                raid_level: Optional[str] = None,
                t10_pi_status: Optional[str] = None,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List or run DellRaidService read-only query actions.

        :param query: selector from ``_QUERY_SPECS``; omit to list targets.
        :param dry_run: resolve the target and payload without POSTing.
        :param block_size: optional BlockSizeInBytes filter.
        :param disk_encrypt: optional DiskEncrypt filter.
        :param disk_type: optional DiskType filter.
        :param disk_protocol: optional Diskprotocol filter.
        :param form_factor: optional FormFactor filter.
        :param raid_level: optional RaidLevel filter for available-disk queries.
        :param t10_pi_status: optional T10PIStatus filter.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue the underlying queries/POST on the async path when True.
        :return: CommandResult with a listing, dry-run preview, execution result,
            or missing-action error.
        """
        service_uri, actions, rows = self._discover_rows(bool(do_async))
        if query is None:
            return CommandResult(rows, actions, None, None)

        spec = _QUERY_SPECS[query]
        row = next((item for item in rows if item["Action"] == query), None)
        if row is None:
            return CommandResult(
                {
                    "raid_service": service_uri,
                    "action": spec.full_type,
                    "available": rows,
                },
                actions,
                None,
                f"Dell RAID query action not found: {query}",
            )

        payload = self._payload_from_args(
            spec,
            {
                "block_size": block_size,
                "disk_encrypt": disk_encrypt,
                "disk_type": disk_type,
                "disk_protocol": disk_protocol,
                "form_factor": form_factor,
                "raid_level": raid_level,
                "t10_pi_status": t10_pi_status,
            },
        )
        return self._invoke_query_action(
            row,
            spec,
            payload,
            do_async=do_async,
            dry_run=bool(dry_run),
        )
