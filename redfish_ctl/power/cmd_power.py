"""Read Redfish Chassis PowerSubsystem resources."""

from abc import abstractmethod
from typing import Optional

from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import REDFISH_API, ApiRequestType, Singleton
from ..redfish_manager import CommandResult


class Power(RedfishManagerBase,
            scm_type=ApiRequestType.Power,
            name="power",
            metaclass=Singleton):
    """Read chassis power subsystems, supplies, metrics, and batteries."""

    def __init__(self, *args, **kwargs):
        super(Power, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the read-only ``power`` subcommand."""
        cmd_parser = cls.base_parser()
        return cmd_parser, "power", "command read chassis power subsystem data"

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
    def _status(data):
        status = data.get("Status") if isinstance(data, dict) else None
        return status if isinstance(status, dict) else {}

    @staticmethod
    def _chassis_id(chassis_uri):
        return chassis_uri.rstrip("/").rsplit("/", 1)[-1]

    @staticmethod
    def _reading(value):
        if isinstance(value, dict):
            return value.get("Reading")
        return value

    @staticmethod
    def _first_present(*values):
        for value in values:
            if value is not None:
                return value
        return None

    def _query_optional(self, uri, do_async=False):
        try:
            return self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}

    def _read_power_supplies(self, chassis_id, supplies_uri, do_async=False):
        data = self._query_optional(supplies_uri, do_async=do_async)
        if not isinstance(data, dict):
            return None, []
        members = data.get("Members") or []
        if not isinstance(members, list):
            members = []
        collection = {
            "Chassis": chassis_id,
            "Name": data.get("Name"),
            "MemberCount": data.get("Members@odata.count", len(members)),
            "Uri": data.get("@odata.id", supplies_uri),
        }
        supplies = []
        for member in members:
            if not isinstance(member, dict):
                continue
            supply_uri = member.get("@odata.id")
            supply = member
            if supply_uri and "Status" not in supply:
                supply = self._query_optional(supply_uri, do_async=do_async)
            if not isinstance(supply, dict):
                continue
            metrics_uri = self._link(supply, "Metrics")
            metrics = {}
            if metrics_uri:
                metrics = self._query_optional(metrics_uri, do_async=do_async)
            status = self._status(supply)
            supplies.append({
                "Chassis": chassis_id,
                "Id": supply.get("Id"),
                "Name": supply.get("Name"),
                "State": status.get("State"),
                "Health": status.get("Health"),
                "Model": supply.get("Model"),
                "Manufacturer": supply.get("Manufacturer"),
                "FirmwareVersion": supply.get("FirmwareVersion"),
                "PowerCapacityWatts": supply.get("PowerCapacityWatts"),
                "LastPowerOutputWatts": supply.get("LastPowerOutputWatts"),
                "LineInputVoltage": supply.get("LineInputVoltage"),
                "LineInputVoltageType": supply.get("LineInputVoltageType"),
                "InputPowerWatts": self._first_present(
                    self._reading(metrics.get("InputPowerWatts")),
                    supply.get("InputPowerWatts"),
                ),
                "OutputPowerWatts": self._first_present(
                    self._reading(metrics.get("OutputPowerWatts")),
                    supply.get("OutputPowerWatts"),
                ),
                "InputVoltage": self._reading(metrics.get("InputVoltage")),
                "InputCurrentAmps": self._reading(
                    metrics.get("InputCurrentAmps")),
                "EnergykWh": self._reading(metrics.get("EnergykWh")),
                "TemperatureCelsius": self._reading(
                    metrics.get("TemperatureCelsius")),
                "Uri": supply.get("@odata.id", supply_uri),
                "MetricsUri": metrics_uri,
            })
        return collection, supplies

    def _read_batteries(self, chassis_id, batteries_uri, do_async=False):
        data = self._query_optional(batteries_uri, do_async=do_async)
        if not isinstance(data, dict):
            return None, []
        members = data.get("Members") or []
        if not isinstance(members, list):
            members = []
        collection = {
            "Chassis": chassis_id,
            "Name": data.get("Name"),
            "MemberCount": data.get("Members@odata.count", len(members)),
            "Uri": data.get("@odata.id", batteries_uri),
        }
        batteries = []
        for member in members:
            if not isinstance(member, dict):
                continue
            battery_uri = member.get("@odata.id")
            battery = member
            if battery_uri and "Status" not in battery:
                battery = self._query_optional(battery_uri, do_async=do_async)
            if not isinstance(battery, dict):
                continue
            status = self._status(battery)
            batteries.append({
                "Chassis": chassis_id,
                "Id": battery.get("Id"),
                "Name": battery.get("Name"),
                "State": status.get("State"),
                "Health": status.get("Health"),
                "CapacityWh": battery.get("CapacityWh"),
                "ChargePercent": battery.get("ChargePercent"),
                "Uri": battery.get("@odata.id", battery_uri),
            })
        return collection, batteries

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
            "power_supply_collections": [],
            "power_supplies": [],
            "battery_collections": [],
            "batteries": [],
        }
        chassis = self.base_query(REDFISH_API.Chassis, do_async=do_async)
        chassis_uris = self._members(chassis.data)

        for chassis_uri in chassis_uris:
            chassis_id = self._chassis_id(chassis_uri)
            chassis_data = self._query_optional(chassis_uri, do_async=do_async)
            power_uri = self._link(chassis_data, "PowerSubsystem")
            if not power_uri:
                continue
            power = self._query_optional(power_uri, do_async=do_async)
            if not isinstance(power, dict) or not power:
                continue
            status = self._status(power)
            allocation = power.get("Allocation")
            if not isinstance(allocation, dict):
                allocation = {}
            supplies_uri = self._link(power, "PowerSupplies")
            batteries_uri = self._link(power, "Batteries")
            data["subsystems"].append({
                "Chassis": chassis_id,
                "Name": power.get("Name"),
                "State": status.get("State"),
                "Health": status.get("Health"),
                "HealthRollup": status.get("HealthRollup"),
                "CapacityWatts": power.get("CapacityWatts"),
                "AllocatedWatts": allocation.get("AllocatedWatts"),
                "RequestedWatts": allocation.get("RequestedWatts"),
                "Uri": power.get("@odata.id", power_uri),
                "PowerSuppliesUri": supplies_uri,
                "BatteriesUri": batteries_uri,
            })

            if supplies_uri:
                collection, supplies = self._read_power_supplies(
                    chassis_id, supplies_uri, do_async=do_async)
                if collection is not None:
                    data["power_supply_collections"].append(collection)
                    data["power_supplies"].extend(supplies)

            if batteries_uri:
                collection, batteries = self._read_batteries(
                    chassis_id, batteries_uri, do_async=do_async)
                if collection is not None:
                    data["battery_collections"].append(collection)
                    data["batteries"].extend(batteries)

        data["summary"] = {
            "chassis": len(chassis_uris),
            "power_subsystems": len(data["subsystems"]),
            "power_supply_collections": len(
                data["power_supply_collections"]),
            "power_supplies": len(data["power_supplies"]),
            "battery_collections": len(data["battery_collections"]),
            "batteries": len(data["batteries"]),
        }
        return CommandResult(data, None, None, None)
