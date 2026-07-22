"""Fetch Redfish SPDM signed measurements.

    redfish_ctl spdm-measurements
    redfish_ctl spdm-measurements --component HGX_ERoT_BMC_0 --dry_run
    redfish_ctl spdm-measurements --component HGX_ERoT_BMC_0 --measurement-index 255

ComponentIntegrity signed-measurement fetches are read-only Redfish actions:
the BMC carries the query over POST but does not mutate host or controller state.
Targets are discovered from each ComponentIntegrity resource's own ``Actions``
block; component ids and action URLs are not hardcoded.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidArgument, ResourceNotFound
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult
from ..redfish_shared import RedfishApi

_SPDM_ACTION = "#ComponentIntegrity.SPDMGetSignedMeasurements"


class SpdmMeasurements(IDracManager,
                       scm_type=ApiRequestType.SpdmMeasurements,
                       name="spdm-measurements",
                       metaclass=Singleton):
    """Fetch signed measurements from SPDM ComponentIntegrity resources."""

    def __init__(self, *args, **kwargs):
        """Initialize the spdm-measurements command."""
        super(SpdmMeasurements, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``spdm-measurements`` subcommand.

        :param cls: the owning command class, used to build the base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--component", required=False, dest="component", type=str,
            default=None,
            help="ComponentIntegrity Id, TargetComponentURI, or full resource URI; "
                 "omit to list SPDM measurement targets")
        cmd_parser.add_argument(
            "--measurement-index", action="append", dest="measurement_indices",
            default=None,
            help="measurement index 0..255; may be repeated or comma-separated")
        cmd_parser.add_argument(
            "--nonce", required=False, dest="nonce", type=str, default=None,
            help="optional SPDM nonce to include in the signed-measurement request")
        cmd_parser.add_argument(
            "--slot-id", required=False, dest="slot_id", type=int, default=None,
            help="optional SPDM slot id to include in the request payload")
        cmd_parser.add_argument(
            "--dry_run", action="store_true", dest="dry_run", default=False,
            help="resolve the target and payload without POSTing")
        return cmd_parser, "spdm-measurements", "command fetch SPDM signed measurements"

    @staticmethod
    def _members(data):
        """Return the ``@odata.id`` strings from a Redfish collection.

        :param data: a Redfish collection body (or any value; non-dicts yield []).
        :return: list of member ``@odata.id`` strings.
        """
        if not isinstance(data, dict):
            return []
        return [m["@odata.id"] for m in data.get("Members", [])
                if isinstance(m, dict) and isinstance(m.get("@odata.id"), str)]

    @staticmethod
    def _action_target(data):
        """Return the SPDMGetSignedMeasurements target from a resource body.

        :param data: ComponentIntegrity resource body.
        :return: action target URI, or None when the action is absent.
        """
        actions = data.get("Actions") if isinstance(data, dict) else None
        action = actions.get(_SPDM_ACTION) if isinstance(actions, dict) else None
        if not isinstance(action, dict):
            return None
        target = action.get("target")
        return target if isinstance(target, str) and target else None

    @staticmethod
    def _action_info(data):
        """Return the SPDM action-info link from a resource body.

        :param data: ComponentIntegrity resource body.
        :return: ActionInfo URI, or None when absent.
        """
        actions = data.get("Actions") if isinstance(data, dict) else None
        action = actions.get(_SPDM_ACTION) if isinstance(actions, dict) else None
        if not isinstance(action, dict):
            return None
        info = action.get("@Redfish.ActionInfo")
        return info if isinstance(info, str) and info else None

    @staticmethod
    def _target_row(uri, data):
        """Build a public target row from one SPDM-capable ComponentIntegrity leaf.

        :param uri: ComponentIntegrity resource URI.
        :param data: decoded ComponentIntegrity resource body.
        :return: row describing the measurement target.
        """
        return {
            "Id": data.get("Id") or uri.rsplit("/", 1)[-1],
            "uri": uri,
            "target": SpdmMeasurements._action_target(data),
            "ActionInfo": SpdmMeasurements._action_info(data),
            "TargetComponentURI": data.get("TargetComponentURI"),
            "Type": data.get("ComponentIntegrityType"),
            "Version": data.get("ComponentIntegrityTypeVersion"),
            "Enabled": data.get("ComponentIntegrityEnabled"),
        }

    @staticmethod
    def _parse_measurement_indices(values):
        """Parse DMTF MeasurementIndices values.

        :param values: repeated or comma-separated CLI values.
        :return: list of integer measurement indices.
        :raises InvalidArgument: when an index is not an integer in 0..255.
        """
        if not values:
            return None
        parsed = []
        for value in values:
            for part in str(value).split(","):
                part = part.strip()
                if not part:
                    continue
                try:
                    index = int(part, 10)
                except ValueError as exc:
                    raise InvalidArgument(
                        f"measurement index must be an integer: {part}") from exc
                if index < 0 or index > 255:
                    raise InvalidArgument(
                        f"measurement index must be between 0 and 255: {index}")
                parsed.append(index)
        return parsed or None

    def _get(self, uri, do_async, optional=False):
        """GET a Redfish resource body.

        :param uri: Redfish resource URI to fetch.
        :param do_async: issue the query on the async Redfish path when True.
        :param optional: when True, a missing resource is treated as an empty dict.
        :return: parsed response body, or {} for an optional missing resource.
        :raises InvalidArgument: when a required read fails or returns non-object data.
        """
        try:
            data = self.base_query(uri, do_async=do_async).data or {}
        except ResourceNotFound:
            if optional:
                return {}
            raise InvalidArgument(f"resource not found: {uri}") from None
        except Exception as exc:
            raise InvalidArgument(f"failed to read {uri}: {exc}") from exc
        if not isinstance(data, dict):
            raise InvalidArgument(f"unexpected response from {uri}: expected object")
        return data

    def _collection_members(self, collection_uri, do_async):
        """Return collection member URIs, following Redfish pagination.

        :param collection_uri: Redfish collection URI to start from.
        :param do_async: issue the underlying queries on the async path when True.
        :return: list of member resource URIs.
        :raises InvalidArgument: when a pagination loop or page read failure occurs.
        """
        members = []
        next_uri = collection_uri
        seen = set()
        while next_uri:
            if next_uri in seen:
                raise InvalidArgument(
                    f"pagination loop while reading {collection_uri}: {next_uri}")
            seen.add(next_uri)
            page_uri = next_uri
            page = self._get(next_uri, do_async)
            members.extend(self._members(page))
            next_uri = page.get("Members@odata.nextLink")
            if next_uri is not None and not isinstance(next_uri, str):
                raise InvalidArgument(
                    f"invalid Members@odata.nextLink in {page_uri}: expected string")
        return members

    def _discover_targets(self, do_async):
        """Discover SPDM signed-measurement targets.

        :param do_async: issue the underlying queries on the async path when True.
        :return: list of target rows ordered by collection discovery.
        """
        targets = []
        for uri in self._collection_members(
            f"{RedfishApi.Version}/ComponentIntegrity",
            do_async,
        ):
            data = self._get(uri, do_async)
            if not self._action_target(data):
                continue
            targets.append(self._target_row(uri, data))
        return targets

    @staticmethod
    def _resolve_component(component, targets):
        """Resolve a component selector to a ComponentIntegrity resource URI.

        :param component: Id, ComponentIntegrity URI, TargetComponentURI, or action target.
        :param targets: discovered SPDM target rows.
        :return: ComponentIntegrity resource URI.
        :raises InvalidArgument: when the component is unknown or ambiguous.
        """
        wanted = component.strip()
        if wanted.startswith("/redfish/"):
            matches = [
                row for row in targets
                if wanted in {row["uri"], row["target"], row.get("TargetComponentURI")}
            ]
        else:
            folded = wanted.lower()
            matches = [row for row in targets if str(row["Id"]).lower() == folded]
        if not matches:
            available = [row["Id"] for row in targets]
            raise InvalidArgument(
                f"no SPDM measurement target for '{component}'; available: {available}")
        if len(matches) > 1:
            uris = [row["uri"] for row in matches]
            raise InvalidArgument(
                f"SPDM measurement target '{component}' is ambiguous; pass a URI: {uris}")
        return matches[0]["uri"]

    @staticmethod
    def _payload(measurement_indices, nonce, slot_id):
        """Build a SPDMGetSignedMeasurements payload from optional arguments.

        :param measurement_indices: parsed MeasurementIndices list, or None.
        :param nonce: optional SPDM nonce.
        :param slot_id: optional SPDM slot id.
        :return: payload dict.
        """
        payload = {}
        if measurement_indices is not None:
            payload["MeasurementIndices"] = measurement_indices
        if nonce:
            payload["Nonce"] = nonce
        if slot_id is not None:
            payload["SlotId"] = slot_id
        return payload

    def execute(self,
                component: Optional[str] = None,
                measurement_indices: Optional[list[str]] = None,
                nonce: Optional[str] = None,
                slot_id: Optional[int] = None,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List SPDM targets, or fetch signed measurements from one target.

        With no component selector, the command lists ComponentIntegrity leaves
        that advertise ``#ComponentIntegrity.SPDMGetSignedMeasurements`` and does
        not POST. With a selector, the command invokes the read-only action; use
        ``--dry_run`` to show the resolved payload without POSTing.

        :param component: Id, ComponentIntegrity URI, TargetComponentURI, or
            action target to fetch measurements from; None lists available targets.
        :param measurement_indices: optional repeated/comma-separated index values.
        :param nonce: optional SPDM nonce.
        :param slot_id: optional SPDM slot id.
        :param dry_run: resolve the target and payload without POSTing.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue the underlying queries/POST on the async path when True.
        :return: CommandResult with target listing, dry-run preview, or POST outcome.
        :raises InvalidArgument: when the component or measurement indices are invalid.
        """
        targets = self._discover_targets(do_async)
        if component is None:
            return CommandResult(
                {"spdm_measurement_targets": targets}, None, None, None)
        if not targets:
            raise InvalidArgument(
                "no SPDM measurement targets found on this Redfish endpoint")

        target_uri = self._resolve_component(component, targets)
        payload = self._payload(
            self._parse_measurement_indices(measurement_indices),
            nonce,
            slot_id,
        )
        return self.invoke_action(
            target_uri,
            "SPDMGetSignedMeasurements",
            payload=payload,
            full_action_type=_SPDM_ACTION,
            do_async=do_async,
            expected_status=202,
            dry_run=bool(dry_run),
        )
