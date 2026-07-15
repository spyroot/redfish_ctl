"""Read or set Redfish identify LED state on Chassis or ComputerSystem resources."""

from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult
from ..redfish_shared import RedfishApi

_COLLECTIONS = {
    "chassis": RedfishApi.Chassis,
    "system": RedfishApi.Systems,
}
_PROPERTIES = {"LocationIndicatorActive", "IndicatorLED"}


class IdentifyLed(RedfishManagerBase,
                  scm_type=ApiRequestType.IdentifyLed,
                  name="identify-led",
                  metaclass=Singleton):
    """Read or set the physical identify LED on a chassis or system."""

    def __init__(self, *args, **kwargs):
        super(IdentifyLed, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded ``identify-led`` subcommand."""
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
        if isinstance(data, dict) and isinstance(data.get("Id"), str):
            return data["Id"]
        return uri.rstrip("/").rsplit("/", 1)[-1]

    @staticmethod
    def _property_for(data, property_name):
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
        if property_name == "LocationIndicatorActive":
            return {property_name: bool(active)}
        return {property_name: "Lit" if active else "Off"}

    @staticmethod
    def _target_matches(uri, resource_id, target_id):
        return resource_id.casefold() == target_id.casefold() or uri == target_id

    def _get(self, uri, do_async):
        result = self.base_query(uri, do_async=do_async)
        if result.error is not None:
            raise InvalidArgument(f"Unable to read {uri}: {result.error}")
        if not isinstance(result.data, dict):
            raise InvalidArgument(f"Unable to read {uri}: expected object response")
        return result.data

    def _resolve(self, resource, target_id, property_name, do_async):
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
        """Read or preview/apply an identify LED state change."""
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
