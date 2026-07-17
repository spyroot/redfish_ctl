"""Preview or run Redfish NetworkAdapter.Reset on a discovered adapter.

    redfish_ctl network-adapter-reset
    redfish_ctl network-adapter-reset --adapter IO_Board_0_CX8_0 --dry_run
    redfish_ctl network-adapter-reset --adapter IO_Board_0_CX8_0 --confirm

The command discovers ``#NetworkAdapter.Reset`` from each NetworkAdapter resource
and previews by default. Use ``--confirm`` only after reviewing the target and
payload, because a network-adapter reset can disrupt host or fabric traffic.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import REDFISH_API, ApiRequestType, Singleton

_NETWORK_ADAPTER_RESET_ACTION = "#NetworkAdapter.Reset"


class NetworkAdapterReset(RedfishManagerBase,
                          scm_type=ApiRequestType.NetworkAdapterReset,
                          name="network-adapter-reset",
                          metaclass=Singleton):
    """Resolve and invoke NetworkAdapter.Reset through the action guard."""

    def __init__(self, *args, **kwargs):
        """Initialize the network-adapter-reset command."""
        super(NetworkAdapterReset, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``network-adapter-reset`` subcommand.

        :param cls: command class used to build the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--adapter",
            default=None,
            help="NetworkAdapter Id or Redfish URI; omit to list reset-capable adapters",
        )
        cmd_parser.add_argument(
            "--reset-type",
            dest="reset_type",
            default=None,
            help="ResetType payload value; defaults to the only advertised value when unambiguous",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            default=False,
            help="POST the adapter reset action instead of previewing it",
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
            "network-adapter-reset",
            "run NetworkAdapter.Reset on a discovered adapter (guarded)",
        )

    @staticmethod
    def _members(data):
        """Return member ``@odata.id`` values from a Redfish collection.

        :param data: Redfish collection body.
        :return: list of member URI strings.
        """
        if not isinstance(data, dict):
            return []
        return [
            member["@odata.id"]
            for member in data.get("Members", [])
            if isinstance(member, dict) and isinstance(member.get("@odata.id"), str)
        ]

    @staticmethod
    def _link(data, key):
        """Return a linked resource URI from a Redfish object.

        :param data: Redfish resource body.
        :param key: link property name.
        :return: linked ``@odata.id`` string, or None.
        """
        link = data.get(key) if isinstance(data, dict) else None
        return link.get("@odata.id") if isinstance(link, dict) else None

    @staticmethod
    def _chassis_id(adapter_uri):
        """Return the chassis id embedded in a NetworkAdapter URI.

        :param adapter_uri: Redfish NetworkAdapter URI.
        :return: chassis id string, or None when the URI is not chassis-scoped.
        """
        parts = adapter_uri.strip("/").split("/")
        if len(parts) >= 6 and parts[0:3] == ["redfish", "v1", "Chassis"]:
            return parts[3]
        return None

    @staticmethod
    def _allowed_reset_types(adapter):
        """Return advertised ResetType values for ``#NetworkAdapter.Reset``.

        :param adapter: Redfish NetworkAdapter resource body.
        :return: list of ResetType strings.
        """
        actions = adapter.get("Actions") if isinstance(adapter, dict) else None
        action = (
            actions.get(_NETWORK_ADAPTER_RESET_ACTION)
            if isinstance(actions, dict)
            else None
        )
        values = (
            action.get("ResetType@Redfish.AllowableValues")
            if isinstance(action, dict)
            else None
        )
        return [value for value in values or [] if isinstance(value, str)]

    def _get(self, uri, do_async):
        """GET a Redfish resource body, returning an empty dict on failure.

        :param uri: Redfish resource URI.
        :param do_async: run the query through the async path when True.
        :return: parsed resource body dict, or ``{}``.
        """
        try:
            data = self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _resettable_adapters(self, do_async):
        """Discover adapters that advertise ``#NetworkAdapter.Reset``.

        :param do_async: run the underlying reads through the async path when True.
        :return: list of reset-capable adapter rows.
        """
        rows = []
        chassis = self._get(REDFISH_API.Chassis, do_async)
        for chassis_uri in self._members(chassis):
            adapters_uri = self._link(
                self._get(chassis_uri, do_async),
                "NetworkAdapters",
            )
            if not adapters_uri:
                continue
            for adapter_uri in self._members(self._get(adapters_uri, do_async)):
                adapter = self._get(adapter_uri, do_async)
                target = self._flatten_action_targets(adapter).get(
                    _NETWORK_ADAPTER_RESET_ACTION
                )
                if not target:
                    continue
                status = adapter.get("Status") if isinstance(adapter, dict) else {}
                rows.append({
                    "Adapter": adapter.get("Id") or adapter_uri.rsplit("/", 1)[-1],
                    "Chassis": self._chassis_id(adapter_uri),
                    "Model": adapter.get("Model"),
                    "Manufacturer": adapter.get("Manufacturer"),
                    "Health": status.get("Health") if isinstance(status, dict) else None,
                    "Resource": adapter_uri,
                    "Target": target,
                    "ResetTypes": self._allowed_reset_types(adapter),
                })
        return rows

    @staticmethod
    def _matches(row, adapter):
        """Return whether a resettable row matches an adapter selector.

        :param row: resettable adapter row.
        :param adapter: adapter id or resource URI supplied by the caller.
        :return: True when the row matches.
        """
        selector = (adapter or "").strip()
        return selector in {
            row.get("Adapter"),
            row.get("Resource"),
            row.get("Resource", "").rstrip("/").rsplit("/", 1)[-1],
        }

    def _resolve_adapter(self, adapter, do_async):
        """Resolve one adapter selector to a resettable adapter row.

        :param adapter: adapter id or Redfish URI.
        :param do_async: run the underlying reads through the async path when True.
        :return: matching resettable adapter row.
        :raises InvalidArgument: when the selector is empty, unknown, or ambiguous.
        """
        if not (adapter or "").strip():
            raise InvalidArgument("network-adapter-reset requires --adapter")
        matches = [
            row for row in self._resettable_adapters(do_async)
            if self._matches(row, adapter)
        ]
        if not matches:
            raise InvalidArgument(
                f"network adapter reset target not found: {adapter}"
            )
        if len(matches) > 1:
            resources = ", ".join(row["Resource"] for row in matches)
            raise InvalidArgument(
                f"ambiguous network adapter '{adapter}'; use one URI: {resources}"
            )
        return matches[0]

    @staticmethod
    def _payload_for(row, reset_type):
        """Build the NetworkAdapter.Reset payload.

        :param row: resettable adapter row with advertised ResetTypes.
        :param reset_type: caller-selected ResetType, or None to use the sole
            advertised value.
        :return: Redfish action payload.
        :raises InvalidArgument: when the selected ResetType is not advertised.
        """
        allowed = row.get("ResetTypes") or []
        selected = reset_type or (allowed[0] if len(allowed) == 1 else None)
        if selected and allowed and selected not in allowed:
            raise InvalidArgument(
                f"invalid ResetType for {row['Adapter']}: {selected}; "
                f"allowed: {', '.join(allowed)}"
            )
        return {"ResetType": selected} if selected else {}

    def execute(self,
                adapter: Optional[str] = None,
                reset_type: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Preview or run NetworkAdapter.Reset for one discovered adapter.

        :param adapter: NetworkAdapter id or Redfish URI; when omitted, list
            reset-capable adapters.
        :param reset_type: optional ResetType payload value.
        :param confirm: actually POST the destructive reset action.
        :param dry_run: force a no-POST preview even when confirm is set.
        :param do_async: run reads and POST through the async path when True.
        :return: CommandResult with either the resettable-adapter list or action
            preview/result metadata.
        """
        if not adapter:
            return CommandResult(
                {"resettable_adapters": self._resettable_adapters(bool(do_async))},
                None,
                None,
                None,
            )

        row = self._resolve_adapter(adapter, bool(do_async))
        result = self.invoke_action(
            row["Resource"],
            "Reset",
            payload=self._payload_for(row, reset_type),
            full_action_type=_NETWORK_ADAPTER_RESET_ACTION,
            do_async=do_async,
            expected_status=202,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
        )
        if isinstance(result.data, dict):
            data = dict(result.data)
            data.setdefault("adapter", row["Adapter"])
            data.setdefault("resource", row["Resource"])
            data.setdefault("reset_types", row["ResetTypes"])
            return CommandResult(data, result.discovered, result.extra, result.error)
        return result
