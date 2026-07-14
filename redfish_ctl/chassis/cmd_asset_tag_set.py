"""Read or set Redfish AssetTag on Chassis or ComputerSystem resources."""

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


class AssetTagSet(RedfishManagerBase,
                  scm_type=ApiRequestType.AssetTagSet,
                  name="asset-tag-set",
                  metaclass=Singleton):
    """Read or set AssetTag on a chassis or system resource."""

    def __init__(self, *args, **kwargs):
        super(AssetTagSet, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded ``asset-tag-set`` subcommand."""
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
            help="resource Id or @odata.id URI to read or patch",
        )
        cmd_parser.add_argument(
            "--asset-tag",
            dest="asset_tag",
            default=None,
            help="AssetTag value to apply; omit to read the current value",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            dest="confirm",
            default=False,
            help="apply the PATCH; without it the command only previews",
        )
        return cmd_parser, "asset-tag-set", "read or set AssetTag"

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

    def _get(self, uri, do_async):
        result = self.base_query(uri, do_async=do_async)
        if result.error:
            raise InvalidArgument(f"failed to query {uri}: {result.error}")
        return result.data or {}

    def _resolve(self, resource, target_id, do_async):
        if resource not in _COLLECTIONS:
            raise InvalidArgument(f"unsupported AssetTag resource {resource!r}")
        if not target_id:
            raise InvalidArgument("target_id is required")

        collection = self._get(_COLLECTIONS[resource], do_async)
        for uri in self._members(collection):
            data = self._get(uri, do_async)
            resource_id = self._resource_id(uri, data)
            if resource_id != target_id and uri != target_id:
                continue
            if "AssetTag" not in data:
                raise InvalidArgument("target does not expose AssetTag")
            return {
                "resource": resource,
                "target_id": resource_id,
                "target": uri,
                "current": data.get("AssetTag"),
            }
        raise InvalidArgument(f"No {resource} resource named {target_id}")

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                resource: Optional[str] = "chassis",
                target_id: Optional[str] = None,
                asset_tag: Optional[str] = None,
                confirm: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Read, preview, or apply an AssetTag value."""
        target = self._resolve(resource, target_id, do_async)
        if asset_tag is None:
            target["read_only"] = True
            return CommandResult(target, None, None, None)

        payload = {"AssetTag": str(asset_tag)}
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
        )
        observed = self._get(target["target"], do_async).get("AssetTag")
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
