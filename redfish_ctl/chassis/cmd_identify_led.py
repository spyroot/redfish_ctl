"""Read or set Redfish identify LED state on Chassis or ComputerSystem resources.

    redfish_ctl identify-led --resource chassis --target-id Chassis_0 --on --confirm
"""

from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult
from ..redfish_shared import RedfishApi

_COLLECTIONS = {
    "chassis": RedfishApi.Chassis,
    "system": RedfishApi.Systems,
}
_PROPERTIES = {"LocationIndicatorActive", "IndicatorLED"}


class IdentifyLed(IDracManager,
                  scm_type=ApiRequestType.IdentifyLed,
                  name="identify-led",
                  metaclass=Singleton):
    """Read or set the physical identify LED on a chassis or system."""

    def __init__(self, *args, **kwargs):
        """Initialize the identify-led command."""
        super(IdentifyLed, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded ``identify-led`` subcommand.

        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--resource",
            choices=sorted(_COLLECTIONS),
            default="chassis",
            help="resource collection to search",
        )
        cmd_parser.add_argument(
            "--target-id",
            required=True,
            dest="target_id",
            help="resource Id to read or patch, such as Chassis_0 or System_0",
        )
        state = cmd_parser.add_mutually_exclusive_group()
        state.add_argument(
            "--on",
            action="store_true",
            dest="active_on",
            default=False,
            help="turn the identify LED on",
        )
        state.add_argument(
            "--off",
            action="store_true",
            dest="active_off",
            default=False,
            help="turn the identify LED off",
        )
        cmd_parser.add_argument(
            "--property",
            choices=sorted(_PROPERTIES),
            dest="property_name",
            default=None,
            help="LED property to patch; defaults to LocationIndicatorActive when present",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            dest="confirm",
            default=False,
            help="apply the PATCH; without it the command only previews",
        )
        return cmd_parser, "identify-led", "read or set identify LED state"

    @staticmethod
    def _members(data):
        """Extract member ``@odata.id`` URIs from a Redfish collection payload.

        :param data: parsed collection response body.
        :return: list of member URI strings; empty when data is not a mapping.
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
    def _resource_id(uri, data):
        """Resolve the resource Id for a member from its payload or URI.

        :param uri: member ``@odata.id`` URI.
        :param data: parsed member response body.
        :return: the payload ``Id`` when present, else the last URI path segment.
        """
        if isinstance(data, dict) and isinstance(data.get("Id"), str):
            return data["Id"]
        return uri.rstrip("/").rsplit("/", 1)[-1]

    @staticmethod
    def _property_for(data, property_name):
        """Choose the identify-LED property to read or patch on a resource.

        :param data: parsed resource body to inspect for LED properties.
        :param property_name: explicit property to use, or None to auto-select.
        :return: the chosen property name (``LocationIndicatorActive`` or
            ``IndicatorLED``).
        :raises InvalidArgument: if the requested property is unsupported or the
            resource exposes no identify-LED property.
        """
        if property_name:
            if property_name not in _PROPERTIES:
                raise InvalidArgument(f"unsupported LED property {property_name!r}")
            if property_name not in data:
                raise InvalidArgument(f"target does not expose {property_name}")
            return property_name
        if "LocationIndicatorActive" in data:
            return "LocationIndicatorActive"
        if "IndicatorLED" in data:
            return "IndicatorLED"
        raise InvalidArgument("target does not expose an identify LED property")

    @staticmethod
    def _payload(property_name, active):
        """Build the PATCH payload for the chosen identify-LED property.

        :param property_name: LED property being set.
        :param active: desired on/off state.
        :return: dict payload — a boolean for ``LocationIndicatorActive``, else
            ``Lit``/``Off`` for ``IndicatorLED``.
        """
        if property_name == "LocationIndicatorActive":
            return {property_name: bool(active)}
        return {property_name: "Lit" if active else "Off"}

    @staticmethod
    def _target_matches(uri, resource_id, target_id):
        """Test whether a member matches the requested target.

        :param uri: member ``@odata.id`` URI.
        :param resource_id: the member's resolved Id.
        :param target_id: requested Id or URI.
        :return: True if the Id matches case-insensitively or the URI matches.
        """
        return resource_id.casefold() == target_id.casefold() or uri == target_id

    def _get(self, uri, do_async):
        """Query a Redfish resource and return its parsed object body.

        :param uri: resource URI to query.
        :param do_async: issue the query on the async event loop when True.
        :return: the parsed response body as a dict.
        :raises InvalidArgument: if the query errors or the body is not an object.
        """
        result = self.base_query(uri, do_async=do_async)
        if result.error is not None:
            raise InvalidArgument(f"Unable to read {uri}: {result.error}")
        if not isinstance(result.data, dict):
            raise InvalidArgument(f"Unable to read {uri}: expected object response")
        return result.data

    def _resolve(self, resource, target_id, property_name, do_async):
        """Locate the target member and its identify-LED property.

        :param resource: collection key, ``chassis`` or ``system``.
        :param target_id: resource Id or ``@odata.id`` URI to match.
        :param property_name: explicit LED property, or None to auto-select.
        :param do_async: issue the queries on the async event loop when True.
        :return: dict with resource, target_id, target URI, property, and current value.
        :raises InvalidArgument: if the resource is unsupported, target_id is
            missing, or no matching member is found.
        """
        if resource not in _COLLECTIONS:
            raise InvalidArgument(f"unsupported identify LED resource {resource!r}")
        if not target_id:
            raise InvalidArgument("target_id is required")

        collection = self._get(_COLLECTIONS[resource], do_async)
        for uri in self._members(collection):
            data = self._get(uri, do_async)
            resource_id = self._resource_id(uri, data)
            if not self._target_matches(uri, resource_id, target_id):
                continue
            led_property = self._property_for(data, property_name)
            return {
                "resource": resource,
                "target_id": resource_id,
                "target": uri,
                "property": led_property,
                "current": data.get(led_property),
            }
        raise InvalidArgument(f"No {resource} resource named {target_id}")

    @staticmethod
    def _active_from_flags(active, active_on, active_off):
        """Reduce the desired LED state from the CLI flags.

        :param active: explicit boolean state, or None to fall back to flags.
        :param active_on: True when ``--on`` was given.
        :param active_off: True when ``--off`` was given.
        :return: the desired boolean state, or None when no state was requested.
        """
        if active is not None:
            return bool(active)
        if active_on:
            return True
        if active_off:
            return False
        return None

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                resource: Optional[str] = "chassis",
                target_id: Optional[str] = None,
                property_name: Optional[str] = None,
                active: Optional[bool] = None,
                active_on: Optional[bool] = False,
                active_off: Optional[bool] = False,
                confirm: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Read or preview/apply an identify LED state change.

        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: note async will subscribe to an event loop.
        :param do_expanded: accepted for CLI compatibility; not used by this command.
        :param resource: collection to search, ``chassis`` or ``system``.
        :param target_id: resource Id or ``@odata.id`` URI to read or patch.
        :param property_name: LED property to patch; auto-selected when omitted.
        :param active: explicit desired state, overriding the on/off flags.
        :param active_on: request the LED on.
        :param active_off: request the LED off.
        :param confirm: apply the PATCH; without it the command only previews.
        :return: CommandResult carrying the resolved target and, by mode, the
            current value (read), a dry-run preview, or the applied PATCH status
            and the observed LED value.
        """
        target = self._resolve(resource, target_id, property_name, do_async)
        desired = self._active_from_flags(active, active_on, active_off)
        if desired is None:
            target["read_only"] = True
            return CommandResult(target, None, None, None)

        payload = self._payload(target["property"], desired)
        if not confirm:
            return CommandResult({
                **target,
                "dry_run": True,
                "note": "preview only; re-run with --confirm to apply",
                "payload": payload,
            }, None, None, None)

        result, status = self.base_patch(
            target["target"],
            payload=payload,
            do_async=do_async,
            expected_status=200,
        )
        observed = self._get(target["target"], do_async).get(target["property"])
        return CommandResult({
            **target,
            "payload": payload,
            "applied": {
                "target": target["target"],
                "status": str(status),
                "error": result.error,
            },
            "observed": observed,
        }, None, None, None)
