"""Read or stage Dell NetworkDeviceFunction network attributes.

    redfish_ctl dell-network-attributes
    redfish_ctl dell-network-attributes --target-id NIC.Slot.2-1-1 --from_spec nic.json
    redfish_ctl dell-network-attributes --target-id NIC.Slot.2-1-1 --from_spec nic.json --confirm

Dell exposes NIC partition attributes below each NetworkDeviceFunction as
``Links.Oem.Dell.DellNetworkAttributes``. This command discovers those links,
resolves their Redfish SettingsObject, and previews a PATCH by default.
"""
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..cmd_utils import from_json_spec
from ..idrac_manager import IDracManager
from ..idrac_shared import REDFISH_API, ApiRequestType, Singleton
from ..redfish_manager import CommandResult


class DellNetworkAttributes(IDracManager,
                            scm_type=ApiRequestType.DellNetworkAttributes,
                            name="dell-network-attributes",
                            metaclass=Singleton):
    """Read or stage Dell NetworkDeviceFunction network attributes."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-network-attributes command."""
        super(DellNetworkAttributes, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded ``dell-network-attributes`` subcommand.

        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--target-id",
            required=False,
            dest="target_id",
            default=None,
            help="NetworkDeviceFunction Id, DellNetworkAttributes URI, or Settings URI",
        )
        cmd_parser.add_argument(
            "-s",
            "--from_spec",
            required=False,
            dest="from_spec",
            default=None,
            metavar="file name",
            help="JSON spec containing an Attributes object to PATCH",
        )
        cmd_parser.add_argument(
            "--apply-time",
            required=False,
            dest="apply_time",
            default=None,
            help="optional @Redfish.SettingsApplyTime ApplyTime value",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            dest="confirm",
            default=False,
            help="apply the PATCH; without it the command only previews",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="resolve the target and payload without PATCHing; overrides --confirm",
        )
        return (
            cmd_parser,
            "dell-network-attributes",
            "read or stage Dell network device attributes",
        )

    @staticmethod
    def _members(data):
        """Return member ``@odata.id`` values from a Redfish collection.

        :param data: parsed Redfish collection payload.
        :return: list of member URI strings.
        """
        if not isinstance(data, dict):
            return []
        return [
            member["@odata.id"]
            for member in data.get("Members", [])
            if isinstance(member, dict)
            and isinstance(member.get("@odata.id"), str)
        ]

    @staticmethod
    def _link(data, *path):
        """Walk nested dict keys and return a linked ``@odata.id`` value.

        :param data: parsed Redfish resource payload.
        :param path: nested property names before the final link object.
        :return: linked URI string, or None.
        """
        node = data
        for key in path:
            if not isinstance(node, dict):
                return None
            node = node.get(key)
        if isinstance(node, dict) and isinstance(node.get("@odata.id"), str):
            return node["@odata.id"]
        return None

    @staticmethod
    def _id(uri, data):
        """Return payload Id when available, otherwise the URI leaf.

        :param uri: Redfish resource URI.
        :param data: parsed Redfish resource payload.
        :return: resource identifier string.
        """
        if isinstance(data, dict) and isinstance(data.get("Id"), str):
            return data["Id"]
        return uri.rstrip("/").rsplit("/", 1)[-1]

    @staticmethod
    def _controller_function_links(adapter):
        """Return NetworkDeviceFunction links nested under adapter controllers.

        :param adapter: parsed NetworkAdapter payload.
        :return: list of NetworkDeviceFunction member URI strings.
        """
        uris = []
        for controller in adapter.get("Controllers", []) if isinstance(adapter, dict) else []:
            links = controller.get("Links") if isinstance(controller, dict) else {}
            members = links.get("NetworkDeviceFunctions", []) if isinstance(links, dict) else []
            for member in members:
                if isinstance(member, dict) and isinstance(member.get("@odata.id"), str):
                    uris.append(member["@odata.id"])
        return uris

    def _get(self, uri, do_async=False, do_expanded=False):
        """Read a Redfish resource and return its parsed body.

        :param uri: Redfish resource URI.
        :param do_async: issue the query on the async path when True.
        :param do_expanded: request expanded output when supported by the caller.
        :return: parsed resource payload.
        :raises InvalidArgument: when the query fails.
        """
        result = self.base_query(uri, do_async=do_async, do_expanded=do_expanded)
        if result.error:
            raise InvalidArgument(f"failed to query {uri}: {result.error}")
        return result.data or {}

    def _function_uris(self, adapter_uri, adapter, do_async, do_expanded):
        """Resolve NetworkDeviceFunction member URIs for one NetworkAdapter.

        :param adapter_uri: NetworkAdapter URI.
        :param adapter: parsed NetworkAdapter payload.
        :param do_async: issue queries on the async path when True.
        :param do_expanded: request expanded collection output when True.
        :return: ordered unique function URI strings.
        """
        seen = set()
        uris = []
        collection_uri = self._link(adapter, "NetworkDeviceFunctions")
        if collection_uri:
            collection = self._get(collection_uri, do_async, do_expanded)
            for uri in self._members(collection):
                if uri not in seen:
                    seen.add(uri)
                    uris.append(uri)
        for uri in self._controller_function_links(adapter):
            if uri not in seen:
                seen.add(uri)
                uris.append(uri)
        if not uris and adapter_uri:
            fallback = f"{adapter_uri.rstrip('/')}/NetworkDeviceFunctions"
            try:
                collection = self._get(fallback, do_async, do_expanded)
            except InvalidArgument:
                return []
            for uri in self._members(collection):
                if uri not in seen:
                    seen.add(uri)
                    uris.append(uri)
        return uris

    def _discover(self, do_async=False, do_expanded=False):
        """Discover DellNetworkAttributes targets from chassis adapter links.

        :param do_async: issue queries on the async path when True.
        :param do_expanded: request expanded collection output when True.
        :return: list of target metadata rows.
        """
        rows = []
        chassis = self._get(REDFISH_API.Chassis, do_async, do_expanded)
        for chassis_uri in self._members(chassis):
            try:
                chassis_data = self._get(chassis_uri, do_async, do_expanded)
            except InvalidArgument:
                continue
            adapters_uri = self._link(chassis_data, "NetworkAdapters")
            if not adapters_uri:
                continue
            try:
                adapters = self._get(adapters_uri, do_async, do_expanded)
            except InvalidArgument:
                continue
            for adapter_uri in self._members(adapters):
                try:
                    adapter = self._get(adapter_uri, do_async, do_expanded)
                except InvalidArgument:
                    continue
                adapter_id = self._id(adapter_uri, adapter)
                for function_uri in self._function_uris(
                        adapter_uri, adapter, do_async, do_expanded):
                    try:
                        function = self._get(function_uri, do_async, do_expanded)
                    except InvalidArgument:
                        continue
                    attrs_uri = self._link(
                        function, "Links", "Oem", "Dell", "DellNetworkAttributes"
                    )
                    if not attrs_uri:
                        attrs_uri = self._link(
                            function, "Oem", "Dell", "DellNetworkAttributes"
                        )
                    if not attrs_uri:
                        continue
                    try:
                        attrs = self._get(attrs_uri, do_async, do_expanded)
                    except InvalidArgument:
                        continue
                    settings_uri = self._link(
                        attrs, "@Redfish.Settings", "SettingsObject"
                    ) or f"{attrs_uri.rstrip('/')}/Settings"
                    try:
                        settings = self._get(settings_uri, do_async, do_expanded)
                    except InvalidArgument:
                        settings = {}
                    current = attrs.get("Attributes", {})
                    pending = settings.get("Attributes", {})
                    redfish_settings = attrs.get("@Redfish.Settings", {})
                    rows.append({
                        "Chassis": chassis_uri.rsplit("/", 1)[-1],
                        "Adapter": adapter_id,
                        "Function": self._id(function_uri, function),
                        "NetworkDeviceFunction": function_uri,
                        "Attributes": attrs_uri,
                        "Settings": settings_uri,
                        "AttributeRegistry": attrs.get("AttributeRegistry"),
                        "SupportedApplyTimes": redfish_settings.get(
                            "SupportedApplyTimes", []
                        ) if isinstance(redfish_settings, dict) else [],
                        "AttributeCount": len(current) if isinstance(current, dict) else 0,
                        "PendingAttributeCount": len(pending) if isinstance(pending, dict) else 0,
                        "CurrentAttributes": current if isinstance(current, dict) else {},
                        "PendingAttributes": pending if isinstance(pending, dict) else {},
                    })
        return rows

    @staticmethod
    def _matches(row, target_id):
        """Return True when target_id identifies a discovered row.

        :param row: discovered target metadata row.
        :param target_id: function Id, NetworkDeviceFunction URI, attributes URI, or settings URI.
        :return: whether the row matches.
        """
        if not target_id:
            return False
        wanted = str(target_id).lower()
        candidates = (
            row.get("Function"),
            row.get("NetworkDeviceFunction"),
            row.get("Attributes"),
            row.get("Settings"),
        )
        return any(str(candidate).lower() == wanted for candidate in candidates if candidate)

    def _target(self, target_id, do_async, do_expanded):
        """Resolve a target row by id or URI.

        :param target_id: function Id or relevant URI.
        :param do_async: issue queries on the async path when True.
        :param do_expanded: request expanded collection output when True.
        :return: matching target row.
        :raises InvalidArgument: when no matching row is found.
        """
        for row in self._discover(do_async, do_expanded):
            if self._matches(row, target_id):
                return row
        raise InvalidArgument(f"No Dell network attributes target named {target_id}")

    @staticmethod
    def _payload(from_spec, target, apply_time=None):
        """Load and validate an attribute PATCH payload.

        :param from_spec: path to a JSON spec with an ``Attributes`` object.
        :param target: resolved target row holding current attributes and apply times.
        :param apply_time: optional SettingsApplyTime value to add.
        :return: validated PATCH payload.
        :raises InvalidArgument: when the payload is malformed or targets unknown attributes.
        """
        if not from_spec:
            raise InvalidArgument("--from_spec is required when patching")
        payload = from_json_spec(from_spec)
        if not isinstance(payload, dict):
            raise InvalidArgument("from_spec must contain a JSON object")
        attrs = payload.get("Attributes")
        if not isinstance(attrs, dict) or not attrs:
            raise InvalidArgument("from_spec must contain a non-empty Attributes object")
        current = target.get("CurrentAttributes", {})
        unknown = sorted(set(attrs) - set(current)) if isinstance(current, dict) else []
        if unknown:
            raise InvalidArgument(
                "unknown Dell network attribute(s) for "
                f"{target['Function']}: {', '.join(unknown)}"
            )
        payload = dict(payload)
        if apply_time:
            supported = target.get("SupportedApplyTimes") or []
            if supported and apply_time not in supported:
                raise InvalidArgument(
                    f"ApplyTime {apply_time!r} is not supported by {target['Function']}; "
                    f"supported values: {', '.join(supported)}"
                )
            payload["@Redfish.SettingsApplyTime"] = {"ApplyTime": apply_time}
        return payload

    @staticmethod
    def _public_target(row):
        """Return target metadata without full attribute payloads.

        :param row: discovered target row.
        :return: compact target metadata for command output.
        """
        return {
            key: value for key, value in row.items()
            if key not in {"CurrentAttributes", "PendingAttributes"}
        }

    def execute(self,
                target_id: Optional[str] = None,
                from_spec: Optional[str] = None,
                apply_time: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List, preview, or apply Dell network attribute settings.

        :param target_id: function Id or target URI. Omit to list targets.
        :param from_spec: JSON spec with ``Attributes`` to PATCH.
        :param apply_time: optional Redfish Settings ApplyTime value.
        :param confirm: actually apply the PATCH when True.
        :param dry_run: force preview mode even when confirm is True.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue the underlying Redfish calls on the async path.
        :param do_expanded: request expanded collection output when supported.
        :return: CommandResult with target list, preview, or PATCH outcome.
        """
        if not target_id:
            rows = [self._public_target(row)
                    for row in self._discover(do_async, do_expanded)]
            return CommandResult(rows, None, None, None)

        target = self._target(target_id, do_async, do_expanded)
        public_target = self._public_target(target)
        if not from_spec:
            return CommandResult({
                **public_target,
                "CurrentAttributes": target.get("CurrentAttributes", {}),
                "PendingAttributes": target.get("PendingAttributes", {}),
                "read_only": True,
            }, None, None, None)

        payload = self._payload(from_spec, target, apply_time)
        if dry_run or not confirm:
            return CommandResult({
                **public_target,
                "dry_run": True,
                "requires_confirm": True,
                "blocked": "Dell network attribute PATCH requires --confirm",
                "payload": payload,
            }, None, None, None)

        result, status = self.base_patch(
            target["Settings"],
            payload=payload,
            do_async=do_async,
        )
        applied = {
            "target": target["Settings"],
            "status": str(status),
            "error": result.error,
        }
        if result.error is not None:
            return CommandResult({
                **public_target,
                "payload": payload,
                "applied": applied,
                "observed": None,
            }, None, None, result.error)

        updated = self._get(target["Settings"], do_async, do_expanded)
        observed = {}
        pending = updated.get("Attributes", {}) if isinstance(updated, dict) else {}
        for attr_name in payload["Attributes"]:
            observed[attr_name] = pending.get(attr_name)
        return CommandResult({
            **public_target,
            "payload": payload,
            "applied": applied,
            "observed": observed,
        }, None, None, None)
