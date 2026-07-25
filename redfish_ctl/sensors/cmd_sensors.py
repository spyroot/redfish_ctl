"""Read Redfish Sensors across all Chassis (generic, vendor-neutral).

    redfish_ctl sensors

Walks ``/redfish/v1/Chassis`` -> each chassis ``Sensors`` collection -> each
Sensor, returning Chassis/Name/Reading/ReadingUnits/Health. Navigation is by
ServiceRoot links and ``@odata.id`` with no hardcoded ids, so it works on any
host exposing the modern Redfish Sensor model (Dell, Supermicro/OpenBMC, HPE).

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import REDFISH_API, ApiRequestType, Singleton
from ..redfish_manager import CommandResult


class Sensors(IDracManager,
              scm_type=ApiRequestType.Sensors,
              name='sensors',
              metaclass=Singleton):
    """Read every Chassis sensor reading (temperature, power, fan, voltage…)."""

    def __init__(self, *args, **kwargs):
        """Initialize the sensors command."""
        super(Sensors, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``sensors`` subcommand (read-only, no flags needed).

        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        return cmd_parser, "sensors", "command read all chassis sensor readings"

    @staticmethod
    def _members(data):
        """Return the @odata.id strings from a Redfish collection, tolerantly.

        :param data: a Redfish collection body (or any value; non-dicts yield []).
        :return: list of member ``@odata.id`` strings.
        """
        if not isinstance(data, dict):
            return []
        return [m["@odata.id"] for m in data.get("Members", [])
                if isinstance(m, dict) and isinstance(m.get("@odata.id"), str)]

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Walk every Chassis Sensors collection and collect readings.

        Tolerant of a chassis without a Sensors link or an unreachable
        collection (skips it). An ``$expand``'d Sensors member already carries
        its Reading; otherwise each member is fetched individually.

        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue the underlying queries on the async event loop when True.
        :param do_expanded: force an expanded ($expand) Sensors query and skip the
            per-member fallback.
        :return: CommandResult whose data is a list of sensor rows
            {Chassis, Name, Reading, ReadingUnits, ReadingType, Health}.
        """
        readings = []
        chassis = self.base_query(REDFISH_API.Chassis, do_async=do_async)
        for chassis_uri in self._members(chassis.data):
            try:
                cdata = self.base_query(chassis_uri, do_async=do_async).data or {}
            except Exception:
                continue
            link = cdata.get("Sensors")
            sensors_uri = link.get("@odata.id") if isinstance(link, dict) else None
            if not sensors_uri:
                continue
            # Prefer $expand: one GET returns every sensor with its Reading, so a
            # large chassis tree -- e.g. a 42-chassis GB300 -- does not fan out into
            # hundreds of per-sensor GETs (that walk times out). Fall back to a plain
            # GET when a BMC rejects $expand (some OpenBMC/iLO builds 400 on it); the
            # per-member loop below then fetches each sensor individually.
            coll = {}
            for want_expand in (True, False):
                if want_expand is False and do_expanded:
                    break  # caller forced expand; don't fall back to slow per-member
                try:
                    coll = self.base_query(sensors_uri, do_async=do_async,
                                           do_expanded=want_expand).data or {}
                    break
                except Exception:
                    coll = {}
            for member in coll.get("Members", []):
                if not isinstance(member, dict):
                    continue
                sd = member if "Reading" in member else None
                if sd is None:
                    uri = member.get("@odata.id")
                    if not uri:
                        continue
                    try:
                        sd = self.base_query(uri, do_async=do_async).data
                    except Exception:
                        continue
                if isinstance(sd, dict) and "Reading" in sd:
                    status = sd.get("Status") or {}
                    readings.append({
                        "Chassis": chassis_uri.rsplit("/", 1)[-1],
                        "Name": sd.get("Name"),
                        "Reading": sd.get("Reading"),
                        "ReadingUnits": sd.get("ReadingUnits"),
                        "ReadingType": sd.get("ReadingType"),
                        "Health": status.get("Health") if isinstance(status, dict) else None,
                    })
        return CommandResult(readings, None, None, None)
