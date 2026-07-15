"""Read NVIDIA PowerSmoothing resources exposed through GPU processors.

    redfish_ctl power-smoothing

Walks ``/redfish/v1/Systems`` -> each system ``Processors`` -> GPU
processors -> ``Oem/Nvidia/PowerSmoothing``, aggregating smoothing state,
preset profiles, and admin override profiles.
"""

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
        """Initialize the power-smoothing command."""
        super(PowerSmoothing, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the read-only ``power-smoothing`` subcommand.

        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        help_text = "command read NVIDIA GPU PowerSmoothing profiles"
        return cmd_parser, "power-smoothing", help_text

    @staticmethod
    def _members(data):
        """Extract member ``@odata.id`` URIs from a Redfish collection.

        :param data: parsed collection resource that may hold ``Members``.
        :return: list of member ``@odata.id`` strings, empty when absent
            or malformed.
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
    def _link(data, key):
        """Return the ``@odata.id`` referenced by ``data[key]``.

        :param data: parsed resource that may hold a link object under ``key``.
        :param key: name of the link property to dereference.
        :return: the ``@odata.id`` string, or None when absent or malformed.
        """
        value = data.get(key) if isinstance(data, dict) else None
        if isinstance(value, dict) and isinstance(value.get("@odata.id"), str):
            return value["@odata.id"]
        return None

    @staticmethod
    def _nested_link(data, *keys):
        """Follow a chain of nested keys and return the final ``@odata.id``.

        :param data: parsed resource to descend into.
        :return: the ``@odata.id`` at the end of the key chain, or None when
            any step is missing or malformed.
        """
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
        """Derive the resource id from a Redfish URI.

        :param uri: resource ``@odata.id`` path.
        :return: the trailing path segment used as the resource id.
        """
        return uri.rstrip("/").rsplit("/", 1)[-1]

    @staticmethod
    def _action_target(data, key):
        """Return the POST ``target`` of a named Redfish action.

        :param data: parsed resource carrying an ``Actions`` block.
        :param key: action name to look up under ``Actions``.
        :return: the action ``target`` URI string, or None when absent.
        """
        actions = data.get("Actions") if isinstance(data, dict) else None
        action = actions.get(key) if isinstance(actions, dict) else None
        if isinstance(action, dict) and isinstance(action.get("target"), str):
            return action["target"]
        return None

    @staticmethod
    def _profile(system_id, gpu_id, profile, fallback_uri):
        """Build a flat profile row from a PowerSmoothing profile resource.

        :param system_id: system id the profile belongs to.
        :param gpu_id: GPU processor id the profile belongs to.
        :param profile: parsed profile resource.
        :param fallback_uri: URI used when the profile omits ``@odata.id``.
        :return: dict of the selected profile setpoint fields.
        """
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
        """Query a URI and return its data, swallowing errors.

        :param uri: Redfish resource path to GET.
        :param do_async: when True, issue the query on the async event loop.
        :return: the response data dict, or an empty dict on any failure.
        """
        try:
            return self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}

    def _read_profile(self, system_id, gpu_id, profile_uri, do_async=False):
        """Read a single PowerSmoothing profile and flatten it.

        :param system_id: system id the profile belongs to.
        :param gpu_id: GPU processor id the profile belongs to.
        :param profile_uri: profile resource URI to GET.
        :param do_async: when True, issue the query on the async event loop.
        :return: the flattened profile dict, or None when the resource is empty.
        """
        profile = self._query_optional(profile_uri, do_async=do_async)
        if not isinstance(profile, dict) or not profile:
            return None
        return self._profile(system_id, gpu_id, profile, profile_uri)

    def _read_preset_profiles(self,
                              system_id,
                              gpu_id,
                              profiles_uri,
                              do_async=False):
        """Read a PresetProfiles collection and its member profiles.

        :param system_id: system id the profiles belong to.
        :param gpu_id: GPU processor id the profiles belong to.
        :param profiles_uri: ``PresetProfiles`` collection URI to walk.
        :param do_async: when True, issue queries on the async event loop.
        :return: tuple of (collection summary dict, list of profile dicts),
            or (None, []) when the collection is empty.
        """
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
        """Read one GPU PowerSmoothing resource and append it to ``data``.

        Appends the smoothing row plus any preset-profile collection and
        admin override profile to the corresponding lists in ``data``.

        :param data: aggregation dict mutated in place with smoothing results.
        :param system_id: system id the GPU belongs to.
        :param gpu_id: GPU processor id being read.
        :param smoothing_uri: ``PowerSmoothing`` resource URI to GET.
        :param do_async: when True, issue queries on the async event loop.
        """
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
        """Read NVIDIA GPU PowerSmoothing state and profiles across systems.

        Walks every ``/redfish/v1/Systems`` member to its GPU processors and
        their ``Oem/Nvidia/PowerSmoothing`` resource, aggregating smoothing
        state, preset profiles, and admin override profiles.

        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this
            command.
        :param do_async: when True, run Redfish queries on the async event
            loop.
        :param do_expanded: accepted for CLI compatibility; not used by this
            command.
        :return: CommandResult holding the aggregated smoothing data and a
            summary.
        """
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
