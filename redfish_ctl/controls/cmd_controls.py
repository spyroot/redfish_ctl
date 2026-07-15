"""Read Redfish Chassis Control resources."""

from abc import abstractmethod
from typing import Optional

from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import REDFISH_API, ApiRequestType, Singleton
from ..redfish_manager import CommandResult


class Controls(RedfishManagerBase,
               scm_type=ApiRequestType.ControlsQuery,
               name="controls",
               metaclass=Singleton):
    """Read chassis Controls collections and member Control setpoints."""

    def __init__(self, *args, **kwargs):
        super(Controls, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the read-only ``controls`` subcommand."""
        cmd_parser = cls.base_parser()
        return cmd_parser, "controls", "command read chassis control data"

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
    def _chassis_id(chassis_uri):
        return chassis_uri.rstrip("/").rsplit("/", 1)[-1]

    @staticmethod
    def _status(data):
        status = data.get("Status") if isinstance(data, dict) else None
        return status if isinstance(status, dict) else {}

    @staticmethod
    def _sensor_reading(control, key):
        sensor = control.get("Sensor") if isinstance(control, dict) else None
        if isinstance(sensor, dict):
            return sensor.get(key)
        return None

    def _query_optional(self, uri, do_async=False):
        try:
            return self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}

    def _read_control_members(self, chassis_id, members, do_async=False):
        controls = []
        for member_uri in members:
            control = self._query_optional(member_uri, do_async=do_async)
            if not isinstance(control, dict) or not control:
                continue
            status = self._status(control)
            controls.append({
                "Chassis": chassis_id,
                "Id": control.get("Id"),
                "Name": control.get("Name"),
                "ControlType": control.get("ControlType"),
                "ControlMode": control.get("ControlMode"),
                "SetPoint": control.get("SetPoint"),
                "SetPointUnits": control.get("SetPointUnits"),
                "DefaultSetPoint": control.get("DefaultSetPoint"),
                "AllowableMin": control.get("AllowableMin"),
                "AllowableMax": control.get("AllowableMax"),
                "Reading": control.get(
                    "Reading", self._sensor_reading(control, "Reading")),
                "ReadingUnits": control.get(
                    "ReadingUnits",
                    self._sensor_reading(control, "ReadingUnits"),
                ),
                "PhysicalContext": control.get("PhysicalContext"),
                "State": status.get("State"),
                "Health": status.get("Health"),
                "Uri": control.get("@odata.id", member_uri),
            })
        return controls

    @staticmethod
    def _summary(chassis_count, collections, controls):
        return {
            "chassis": chassis_count,
            "control_collections": len(collections),
            "controls": len(controls),
            "power_controls": sum(
                1 for row in controls if row["ControlType"] == "Power"),
            "frequency_controls": sum(
                1 for row in controls
                if row["ControlType"] in {"Frequency", "FrequencyMHz"}
            ),
        }

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Read Control resources linked from chassis members."""
        chassis = self.base_query(REDFISH_API.Chassis, do_async=do_async)
        chassis_uris = self._members(chassis.data)
        control_collections = []
        controls = []

        for chassis_uri in chassis_uris:
            chassis_id = self._chassis_id(chassis_uri)
            chassis_data = self._query_optional(chassis_uri, do_async=do_async)
            controls_uri = self._link(chassis_data, "Controls")
            if not controls_uri:
                continue
            collection = self._query_optional(controls_uri, do_async=do_async)
            if not isinstance(collection, dict) or not collection:
                continue
            members = self._members(collection)
            control_collections.append({
                "Chassis": chassis_id,
                "Name": collection.get("Name"),
                "MemberCount": collection.get(
                    "Members@odata.count", len(members)),
                "Uri": collection.get("@odata.id", controls_uri),
            })
            controls.extend(
                self._read_control_members(
                    chassis_id,
                    members,
                    do_async=do_async,
                )
            )

        data = {
            "summary": self._summary(
                len(chassis_uris),
                control_collections,
                controls,
            ),
            "control_collections": control_collections,
            "controls": controls,
        }
        return CommandResult(data, None, None, None)
