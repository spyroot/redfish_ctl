"""Guard NVIDIA WorkloadPower profile enable and disable actions."""

import re
from abc import abstractmethod
from typing import Optional

from ..base_manager import CommandBase
from ..cmd_exceptions import InvalidArgument
from ..command_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult
from ..redfish_shared import RedfishApi

_PROFILE_MASK = re.compile(r"^0x[0-9a-fA-F]+$")
_ACTION_TYPES = {
    "enable": "#NvidiaWorkloadPower.EnableProfiles",
    "disable": "#NvidiaWorkloadPower.DisableProfiles",
}


def _normalize_profile_mask(profile_mask: str) -> str:
    mask = str(profile_mask or "").strip()
    if not _PROFILE_MASK.fullmatch(mask):
        raise InvalidArgument("profile mask must be a hex value like 0x1")
    value = int(mask, 16)
    if value <= 0:
        raise InvalidArgument("profile mask must select at least one profile bit")
    return f"0x{value:x}"


class WorkloadPower(CommandBase,
                    scm_type=ApiRequestType.WorkloadPower,
                    name="workload-power",
                    metaclass=Singleton):
    """Preview or apply NVIDIA WorkloadPower profile actions."""

    def __init__(self, *args, **kwargs):
        super(WorkloadPower, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded ``workload-power`` subcommand."""
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--gpu", dest="gpu_id", required=True, metavar="ID",
            help="GPU processor id exposing the WorkloadPowerProfile resource")
        cmd_parser.add_argument(
            "--profile-mask", dest="profile_mask", required=True, metavar="HEX",
            help="profile bit mask to enable or disable, for example 0x1")
        cmd_parser.add_argument(
            "--mode", choices=sorted(_ACTION_TYPES), required=True,
            help="choose whether to enable or disable the profile mask")
        cmd_parser.add_argument(
            "--confirm", action="store_true", dest="confirm", default=False,
            help="POST the action; without it the command only previews")
        cmd_parser.add_argument(
            "--dry_run", action="store_true", dest="dry_run", default=False,
            help="force preview mode even when --confirm is present")
        help_text = "enable or disable NVIDIA WorkloadPower profile masks"
        return cmd_parser, "workload-power", help_text

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
    def _link(data, key):
        value = data.get(key) if isinstance(data, dict) else None
        if isinstance(value, dict) and isinstance(value.get("@odata.id"), str):
            return value["@odata.id"]
        return None

    @staticmethod
    def _nested_link(data, *keys):
        value = data
        for key in keys:
            if not isinstance(value, dict):
                return None
            value = value.get(key)
        if isinstance(value, dict) and isinstance(value.get("@odata.id"), str):
            return value["@odata.id"]
        return None

    @staticmethod
    def _resource_id(uri):
        return uri.rstrip("/").rsplit("/", 1)[-1]

    def _get(self, uri, do_async=False):
        try:
            return self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}

    def _workload_power_resources(self, do_async=False):
        resources = []
        systems = self._get(RedfishApi.Systems, do_async=do_async)
        for system_uri in self._members(systems):
            system = self._get(system_uri, do_async=do_async)
            processors_uri = self._link(system, "Processors")
            if not processors_uri:
                continue
            processors = self._get(processors_uri, do_async=do_async)
            for processor_uri in self._members(processors):
                processor = self._get(processor_uri, do_async=do_async)
                if processor.get("ProcessorType") != "GPU":
                    continue
                workload_uri = self._nested_link(
                    processor,
                    "Oem",
                    "Nvidia",
                    "WorkloadPowerProfile",
                )
                if workload_uri:
                    resources.append({
                        "System": self._resource_id(system_uri),
                        "GPU": processor.get("Id") or self._resource_id(
                            processor_uri),
                        "Uri": workload_uri,
                    })
        return resources

    def _select_workload_power(self, gpu_id, do_async=False):
        if not gpu_id:
            raise InvalidArgument("GPU id is required")
        for resource in self._workload_power_resources(do_async=do_async):
            if resource["GPU"] == gpu_id:
                return resource
        raise InvalidArgument(
            f"GPU {gpu_id!r} has no NVIDIA WorkloadPowerProfile resource")

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                gpu_id: Optional[str] = None,
                profile_mask: Optional[str] = None,
                mode: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Preview or POST one NVIDIA WorkloadPower profile-mask action."""
        if mode not in _ACTION_TYPES:
            raise InvalidArgument("mode must be 'enable' or 'disable'")

        resource = self._select_workload_power(gpu_id, do_async=do_async)
        payload = {"ProfileMask": _normalize_profile_mask(profile_mask)}
        full_action_type = _ACTION_TYPES[mode]
        result = self.invoke_action(
            resource["Uri"],
            full_action_type.rsplit(".", 1)[-1],
            payload=payload,
            full_action_type=full_action_type,
            do_async=do_async,
            expected_status=202,
            dry_run=bool(dry_run) or not bool(confirm),
            confirm=bool(confirm),
        )

        data = result.data if isinstance(result.data, dict) else {}
        data.update({
            "gpu": resource["GPU"],
            "system": resource["System"],
            "resource": resource["Uri"],
            "mode": mode,
            "profile_mask": payload["ProfileMask"],
        })
        return CommandResult(data, result.discovered, result.extra, result.error)
