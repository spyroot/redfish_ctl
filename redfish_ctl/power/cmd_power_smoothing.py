"""Read NVIDIA PowerSmoothing resources exposed through GPU processors."""

from abc import abstractmethod
from typing import Optional

from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult
from ..redfish_shared import RedfishApi


class PowerSmoothing(RedfishManagerBase,
                     scm_type=ApiRequestType.PowerSmoothing,
                     name="power-smoothing",
                     metaclass=Singleton):
    """Read GPU PowerSmoothing state and profile setpoints."""

    def __init__(self, *args, **kwargs):
        super(PowerSmoothing, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the read-only ``power-smoothing`` subcommand."""
        cmd_parser = cls.base_parser()
        help_text = "command read NVIDIA GPU PowerSmoothing profiles"
        return cmd_parser, "power-smoothing", help_text

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

    @staticmethod
    def _action_target(data, key):
        actions = data.get("Actions") if isinstance(data, dict) else None
        action = actions.get(key) if isinstance(actions, dict) else None
        if isinstance(action, dict) and isinstance(action.get("target"), str):
            return action["target"]
        return None

    @staticmethod
    def _profile(system_id, gpu_id, profile, fallback_uri):
        return {
            "System": system_id,
            "GPU": gpu_id,
            "Id": profile.get("Id"),
            "Name": profile.get("Name"),
            "RampDownHysteresisSeconds": profile.get(
                "RampDownHysteresisSeconds"),
            "RampDownWattsPerSecond": profile.get(
                "RampDownWattsPerSecond"),
            "RampUpWattsPerSecond": profile.get("RampUpWattsPerSecond"),
            "TMPFloorPercent": profile.get("TMPFloorPercent"),
            "Uri": profile.get("@odata.id", fallback_uri),
        }

    def _query_optional(self, uri, do_async=False):
        try:
            return self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}

    def _read_profile(self, system_id, gpu_id, profile_uri, do_async=False):
        profile = self._query_optional(profile_uri, do_async=do_async)
        if not isinstance(profile, dict) or not profile:
            return None
        return self._profile(system_id, gpu_id, profile, profile_uri)

    def _read_preset_profiles(self,
                              system_id,
                              gpu_id,
                              profiles_uri,
                              do_async=False):
        collection = self._query_optional(profiles_uri, do_async=do_async)
        if not isinstance(collection, dict) or not collection:
            return None, []
        members = collection.get("Members") or []
        if not isinstance(members, list):
            members = []
        collection_row = {
            "System": system_id,
            "GPU": gpu_id,
            "Name": collection.get("Name"),
            "MemberCount": collection.get(
                "Members@odata.count",
                len(members),
            ),
            "Uri": collection.get("@odata.id", profiles_uri),
        }

        profiles = []
        for profile_uri in self._members(collection):
            profile = self._read_profile(
                system_id,
                gpu_id,
                profile_uri,
                do_async=do_async,
            )
            if profile is not None:
                profiles.append(profile)
        return collection_row, profiles

    def _append_gpu_power_smoothing(self,
                                    data,
                                    system_id,
                                    gpu_id,
                                    smoothing_uri,
                                    do_async=False):
        smoothing = self._query_optional(smoothing_uri, do_async=do_async)
        if not isinstance(smoothing, dict) or not smoothing:
            return

        admin_uri = self._link(smoothing, "AdminOverrideProfile")
        profiles_uri = self._link(smoothing, "PresetProfiles")
        applied_uri = self._link(smoothing, "AppliedPresetProfile")
        data["power_smoothing"].append({
            "System": system_id,
            "GPU": gpu_id,
            "Name": smoothing.get("Name"),
            "Enabled": smoothing.get("Enabled"),
            "PowerSmoothingSupported": smoothing.get(
                "PowerSmoothingSupported"),
            "ImmediateRampDown": smoothing.get("ImmediateRampDown"),
            "RampDownHysteresisSeconds": smoothing.get(
                "RampDownHysteresisSeconds"),
            "RampDownWattsPerSecond": smoothing.get(
                "RampDownWattsPerSecond"),
            "RampUpWattsPerSecond": smoothing.get("RampUpWattsPerSecond"),
            "TMPFloorPercent": smoothing.get("TMPFloorPercent"),
            "TMPFloorWatts": smoothing.get("TMPFloorWatts"),
            "TMPWatts": smoothing.get("TMPWatts"),
            "RemainingLifetimeCircuitryPercent": smoothing.get(
                "RemainingLifetimeCircuitryPercent"),
            "AppliedPresetProfileUri": applied_uri,
            "AdminOverrideProfileUri": admin_uri,
            "PresetProfilesUri": profiles_uri,
            "ActivatePresetProfileTarget": self._action_target(
                smoothing,
                "#NvidiaPowerSmoothing.ActivatePresetProfile",
            ),
            "ApplyAdminOverridesTarget": self._action_target(
                smoothing,
                "#NvidiaPowerSmoothing.ApplyAdminOverrides",
            ),
            "Uri": smoothing.get("@odata.id", smoothing_uri),
        })

        if profiles_uri:
            collection, profiles = self._read_preset_profiles(
                system_id,
                gpu_id,
                profiles_uri,
                do_async=do_async,
            )
            if collection is not None:
                data["preset_collections"].append(collection)
                data["preset_profiles"].extend(profiles)

        if admin_uri:
            admin_profile = self._read_profile(
                system_id,
                gpu_id,
                admin_uri,
                do_async=do_async,
            )
            if admin_profile is not None:
                data["admin_override_profiles"].append(admin_profile)

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        data = {
            "summary": {},
            "power_smoothing": [],
            "preset_collections": [],
            "preset_profiles": [],
            "admin_override_profiles": [],
        }

        systems = self._query_optional(RedfishApi.Systems, do_async=do_async)
        system_uris = self._members(systems)
        gpu_processors = 0
        for system_uri in system_uris:
            system = self._query_optional(system_uri, do_async=do_async)
            processors_uri = self._link(system, "Processors")
            if not processors_uri:
                continue
            processors = self._query_optional(
                processors_uri,
                do_async=do_async,
            )
            for processor_uri in self._members(processors):
                processor = self._query_optional(
                    processor_uri,
                    do_async=do_async,
                )
                if processor.get("ProcessorType") != "GPU":
                    continue
                gpu_processors += 1
                system_id = self._resource_id(system_uri)
                gpu_id = processor.get("Id") or self._resource_id(
                    processor_uri)
                smoothing_uri = self._nested_link(
                    processor,
                    "Oem",
                    "Nvidia",
                    "PowerSmoothing",
                )
                if smoothing_uri:
                    self._append_gpu_power_smoothing(
                        data,
                        system_id,
                        gpu_id,
                        smoothing_uri,
                        do_async=do_async,
                    )

        data["summary"] = {
            "systems": len(system_uris),
            "gpu_processors": gpu_processors,
            "power_smoothing_resources": len(data["power_smoothing"]),
            "supported": sum(
                1 for row in data["power_smoothing"]
                if row.get("PowerSmoothingSupported") is True
            ),
            "enabled": sum(
                1 for row in data["power_smoothing"]
                if row.get("Enabled") is True
            ),
            "preset_collections": len(data["preset_collections"]),
            "preset_profiles": len(data["preset_profiles"]),
            "admin_override_profiles": len(data["admin_override_profiles"]),
        }
        return CommandResult(data, None, None, None)
