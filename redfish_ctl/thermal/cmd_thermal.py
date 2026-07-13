"""Read Redfish Chassis ThermalSubsystem resources."""

from abc import abstractmethod
from typing import Optional

from ..base_manager import CommandBase
from ..command_shared import ApiRequestType, RedfishEndpoint, Singleton
from ..redfish_manager import CommandResult


class Thermal(CommandBase,
              scm_type=ApiRequestType.Thermal,
              name="thermal",
              metaclass=Singleton):
    """Read chassis thermal subsystems, thermal metrics, and fan links."""

    def __init__(self, *args, **kwargs):
        super(Thermal, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the read-only ``thermal`` subcommand."""
        cmd_parser = cls.base_parser()
        return cmd_parser, "thermal", "command read chassis thermal subsystem data"

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

    def _query_optional(self, uri, do_async=False):
        try:
            return self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}

    def _read_fans(self, chassis_id, fans_uri, do_async=False):
        data = self._query_optional(fans_uri, do_async=do_async)
        if not isinstance(data, dict):
            return None, []
        members = data.get("Members") or []
        if not isinstance(members, list):
            members = []
        collection = {
            "Chassis": chassis_id,
            "Name": data.get("Name"),
            "MemberCount": data.get("Members@odata.count", len(members)),
            "Uri": data.get("@odata.id", fans_uri),
        }
        fans = []
        for member in members:
            if not isinstance(member, dict):
                continue
            fan_uri = member.get("@odata.id")
            fan = member
            if fan_uri and "Status" not in fan:
                fan = self._query_optional(fan_uri, do_async=do_async)
            if not isinstance(fan, dict):
                continue
            status = self._status(fan)
            fans.append({
                "Chassis": chassis_id,
                "Name": fan.get("Name"),
                "State": status.get("State"),
                "Health": status.get("Health"),
                "SpeedPercent": fan.get("SpeedPercent"),
                "Uri": fan.get("@odata.id", fan_uri),
            })
        return collection, fans

    @staticmethod
    def _temperature_rows(chassis_id, metrics):
        readings = metrics.get("TemperatureReadingsCelsius") or []
        if not isinstance(readings, list):
            return []
        rows = []
        for reading in readings:
            if not isinstance(reading, dict):
                continue
            rows.append({
                "Chassis": chassis_id,
                "DeviceName": reading.get("DeviceName"),
                "PhysicalContext": reading.get("PhysicalContext"),
                "ReadingCelsius": reading.get("Reading"),
                "DataSourceUri": reading.get("DataSourceUri"),
            })
        return rows

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
            "thermal_metrics": [],
            "temperature_readings": [],
            "fan_collections": [],
            "fans": [],
        }
        chassis = self.base_query(RedfishEndpoint.Chassis, do_async=do_async)
        chassis_uris = self._members(chassis.data)

        for chassis_uri in chassis_uris:
            chassis_id = self._chassis_id(chassis_uri)
            chassis_data = self._query_optional(chassis_uri, do_async=do_async)
            thermal_uri = self._link(chassis_data, "ThermalSubsystem")
            if not thermal_uri:
                continue
            thermal = self._query_optional(thermal_uri, do_async=do_async)
            if not isinstance(thermal, dict) or not thermal:
                continue
            status = self._status(thermal)
            metrics_uri = self._link(thermal, "ThermalMetrics")
            fans_uri = self._link(thermal, "Fans")
            data["subsystems"].append({
                "Chassis": chassis_id,
                "Name": thermal.get("Name"),
                "State": status.get("State"),
                "Health": status.get("Health"),
                "HealthRollup": status.get("HealthRollup"),
                "Uri": thermal.get("@odata.id", thermal_uri),
                "ThermalMetricsUri": metrics_uri,
                "FansUri": fans_uri,
            })

            if metrics_uri:
                metrics = self._query_optional(metrics_uri, do_async=do_async)
                if isinstance(metrics, dict) and metrics:
                    temps = self._temperature_rows(chassis_id, metrics)
                    data["thermal_metrics"].append({
                        "Chassis": chassis_id,
                        "Name": metrics.get("Name"),
                        "Uri": metrics.get("@odata.id", metrics_uri),
                        "TemperatureReadingsCount": len(temps),
                    })
                    data["temperature_readings"].extend(temps)

            if fans_uri:
                fan_collection, fans = self._read_fans(
                    chassis_id, fans_uri, do_async=do_async)
                if fan_collection is not None:
                    data["fan_collections"].append(fan_collection)
                    data["fans"].extend(fans)

        data["summary"] = {
            "chassis": len(chassis_uris),
            "thermal_subsystems": len(data["subsystems"]),
            "thermal_metrics": len(data["thermal_metrics"]),
            "fan_collections": len(data["fan_collections"]),
            "fans": len(data["fans"]),
            "temperature_readings": len(data["temperature_readings"]),
        }
        return CommandResult(data, None, None, None)
