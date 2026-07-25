"""Read Redfish Chassis Control resources.

Example:
    redfish_ctl controls
"""

from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import REDFISH_API, ApiRequestType, Singleton
from ..redfish_manager import CommandResult


class Controls(IDracManager,
               scm_type=ApiRequestType.ControlsQuery,
               name="controls",
               metaclass=Singleton):
    """Read chassis Controls collections and member Control setpoints."""

    def __init__(self, *args, **kwargs):
        """Initialize the controls command."""
        super(Controls, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the read-only ``controls`` subcommand.

        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        return cmd_parser, "controls", "command read chassis control data"

    @staticmethod
    def _members(data):
        """Extract member ``@odata.id`` links from a Redfish collection.

        :param data: collection payload expected to hold a ``Members`` array.
        :return: list of member ``@odata.id`` strings; empty if data is not a dict.
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
        """Return the ``@odata.id`` of a linked resource under a given key.

        :param data: resource payload to read the link from.
        :param key: name of the property holding the linked resource object.
        :return: the linked ``@odata.id`` string, or None when absent.
        """
        value = data.get(key) if isinstance(data, dict) else None
        if isinstance(value, dict) and isinstance(value.get("@odata.id"), str):
            return value["@odata.id"]
        return None

    @staticmethod
    def _chassis_id(chassis_uri):
        """Derive the chassis identifier from a chassis URI.

        :param chassis_uri: chassis ``@odata.id`` path.
        :return: the last path segment of the URI.
        """
        return chassis_uri.rstrip("/").rsplit("/", 1)[-1]

    @staticmethod
    def _status(data):
        """Return the ``Status`` sub-object of a resource.

        :param data: resource payload to read ``Status`` from.
        :return: the ``Status`` dict, or an empty dict when absent.
        """
        status = data.get("Status") if isinstance(data, dict) else None
        return status if isinstance(status, dict) else {}

    @staticmethod
    def _sensor_reading(control, key):
        """Read a value from a Control's embedded ``Sensor`` object.

        :param control: control payload that may hold a ``Sensor`` sub-object.
        :param key: name of the sensor field to read.
        :return: the sensor field value, or None when the sensor is absent.
        """
        sensor = control.get("Sensor") if isinstance(control, dict) else None
        if isinstance(sensor, dict):
            return sensor.get(key)
        return None

    def _query_optional(self, uri, do_async=False):
        """Query a URI, returning an empty dict instead of raising on failure.

        :param uri: Redfish resource URI to query.
        :param do_async: when True, issue the query asynchronously.
        :return: the response data dict, or an empty dict on any error.
        """
        try:
            return self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}

    def _read_control_members(self, chassis_id, members, do_async=False):
        """Query each Control member and flatten it into a summary row.

        :param chassis_id: identifier of the chassis owning the controls.
        :param members: list of Control member URIs to query.
        :param do_async: when True, issue the member queries asynchronously.
        :return: list of dicts describing each reachable Control.
        """
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
        """Build aggregate counts across chassis, collections, and controls.

        :param chassis_count: number of chassis discovered.
        :param collections: list of Control collection rows.
        :param controls: list of flattened Control rows.
        :return: dict with chassis, collection, and control-type counts.
        """
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
        """Read Control resources linked from chassis members.

        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: when True, issue the Redfish queries asynchronously.
        :param do_expanded: accepted for CLI compatibility; not used by this command.
        :return: CommandResult wrapping a dict with the control summary,
            collections, and flattened controls.
        """
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
