"""Read Redfish LeakDetection and LeakDetector resources."""

from abc import abstractmethod
from typing import Optional

from ..base_manager import CommandBase
from ..command_shared import ApiRequestType, RedfishEndpoint, Singleton
from ..redfish_manager import CommandResult


class LeakDetectors(CommandBase,
                    scm_type=ApiRequestType.LeakDetectors,
                    name="leak-detectors",
                    metaclass=Singleton):
    """Read chassis leak detection state and linked leak policies."""

    def __init__(self, *args, **kwargs):
        super(LeakDetectors, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the read-only ``leak-detectors`` subcommand."""
        cmd_parser = cls.base_parser()
        help_text = "command read chassis LeakDetection detector state"
        return cmd_parser, "leak-detectors", help_text

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
    def _status(data):
        status = data.get("Status") if isinstance(data, dict) else None
        return status if isinstance(status, dict) else {}

    @staticmethod
    def _chassis_id(chassis_uri):
        return chassis_uri.rstrip("/").rsplit("/", 1)[-1]

    def _query_optional(self, uri, do_async=False):
        try:
            return self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}

    def _read_detectors(self, chassis_id, detectors_uri, do_async=False):
        data = self._query_optional(detectors_uri, do_async=do_async)
        if not isinstance(data, dict):
            return None, []
        members = data.get("Members") or []
        if not isinstance(members, list):
            members = []
        collection = {
            "Chassis": chassis_id,
            "Name": data.get("Name"),
            "MemberCount": data.get("Members@odata.count", len(members)),
            "Uri": data.get("@odata.id", detectors_uri),
        }
        detectors = []
        for member in members:
            if not isinstance(member, dict):
                continue
            detector_uri = member.get("@odata.id")
            detector = member
            if detector_uri and "DetectorState" not in detector:
                detector = self._query_optional(
                    detector_uri,
                    do_async=do_async,
                )
            if not isinstance(detector, dict):
                continue
            status = self._status(detector)
            detectors.append({
                "Chassis": chassis_id,
                "Id": detector.get("Id"),
                "Name": detector.get("Name"),
                "DetectorState": detector.get("DetectorState"),
                "LeakDetectorType": detector.get("LeakDetectorType"),
                "State": status.get("State"),
                "Health": status.get("Health"),
                "Uri": detector.get("@odata.id", detector_uri),
            })
        return collection, detectors

    @staticmethod
    def _is_leak_policy(policy):
        if not isinstance(policy, dict):
            return False
        values = [
            policy.get("Id"),
            policy.get("Name"),
            policy.get("@odata.type"),
        ]
        for value in values:
            if isinstance(value, str) and "leak" in value.lower():
                return True
        conditions = policy.get("PolicyConditions") or []
        if not isinstance(conditions, list):
            return False
        for condition in conditions:
            if not isinstance(condition, dict):
                continue
            prop = condition.get("Property")
            if isinstance(prop, str) and "leak" in prop.lower():
                return True
        return False

    def _read_policies(self, chassis_id, policies_uri, do_async=False):
        data = self._query_optional(policies_uri, do_async=do_async)
        if not isinstance(data, dict):
            return []
        policies = []
        for policy_uri in self._members(data):
            policy = self._query_optional(policy_uri, do_async=do_async)
            if not self._is_leak_policy(policy):
                continue
            status = self._status(policy)
            conditions = policy.get("PolicyConditions") or []
            if not isinstance(conditions, list):
                conditions = []
            reactions = policy.get("PolicyReactions") or []
            if not isinstance(reactions, list):
                reactions = []
            policies.append({
                "Chassis": chassis_id,
                "Id": policy.get("Id"),
                "Name": policy.get("Name"),
                "PolicyEnabled": policy.get("PolicyEnabled"),
                "PolicyConditionLogic": policy.get("PolicyConditionLogic"),
                "State": status.get("State"),
                "Health": status.get("Health"),
                "ConditionCount": len(conditions),
                "ReactionCount": len(reactions),
                "Conditions": conditions,
                "Reactions": reactions,
                "Uri": policy.get("@odata.id", policy_uri),
            })
        return policies

    @staticmethod
    def _summary(chassis_count, data):
        states = [
            row.get("DetectorState")
            for row in data["detectors"]
            if row.get("DetectorState") is not None
        ]
        states_lower = [
            state.lower()
            for state in states
            if isinstance(state, str)
        ]
        return {
            "chassis": chassis_count,
            "leak_detection_subsystems": len(data["subsystems"]),
            "detector_collections": len(data["detector_collections"]),
            "detectors": len(data["detectors"]),
            "detectors_ok": states_lower.count("ok"),
            "detectors_warning": states_lower.count("warning"),
            "detectors_critical": states_lower.count("critical"),
            "policies": len(data["policies"]),
            "enabled_policies": sum(
                1 for row in data["policies"]
                if row.get("PolicyEnabled") is True
            ),
        }

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        data = {
            "summary": {},
            "subsystems": [],
            "detector_collections": [],
            "detectors": [],
            "policies": [],
        }
        chassis = self.base_query(RedfishEndpoint.Chassis, do_async=do_async)
        chassis_uris = self._members(chassis.data)

        for chassis_uri in chassis_uris:
            chassis_id = self._chassis_id(chassis_uri)
            chassis_data = self._query_optional(chassis_uri, do_async=do_async)
            thermal_uri = self._link(chassis_data, "ThermalSubsystem")
            thermal = {}
            if thermal_uri:
                thermal = self._query_optional(thermal_uri, do_async=do_async)
            leak_uri = self._link(thermal, "LeakDetection")
            if leak_uri:
                leak = self._query_optional(leak_uri, do_async=do_async)
                if isinstance(leak, dict) and leak:
                    status = self._status(leak)
                    detectors_uri = self._link(leak, "LeakDetectors")
                    data["subsystems"].append({
                        "Chassis": chassis_id,
                        "Name": leak.get("Name"),
                        "State": status.get("State"),
                        "Health": status.get("Health"),
                        "HealthRollup": status.get("HealthRollup"),
                        "Uri": leak.get("@odata.id", leak_uri),
                        "LeakDetectorsUri": detectors_uri,
                    })
                    if detectors_uri:
                        collection, detectors = self._read_detectors(
                            chassis_id,
                            detectors_uri,
                            do_async=do_async,
                        )
                        if collection is not None:
                            data["detector_collections"].append(collection)
                            data["detectors"].extend(detectors)

            policies_uri = self._nested_link(
                chassis_data,
                "Oem",
                "Nvidia",
                "Policies",
            )
            if policies_uri:
                data["policies"].extend(
                    self._read_policies(
                        chassis_id,
                        policies_uri,
                        do_async=do_async,
                    )
                )

        data["summary"] = self._summary(len(chassis_uris), data)
        return CommandResult(data, None, None, None)
