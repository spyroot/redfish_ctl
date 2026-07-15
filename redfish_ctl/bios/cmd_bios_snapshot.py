"""Capture a BIOS restore point for transactional, rollback-able changes.

    redfish_ctl bios-snapshot --from_spec change.json -f rollback.json
    redfish_ctl bios-snapshot --attr_name ProcCStates,LogicalProc
    redfish_ctl bios-snapshot            # snapshot every attribute

Reads the host's CURRENT ``Bios.Attributes`` and writes them back out as a
``{"Attributes": {...}}`` spec — the exact format ``bios-change --from_spec``
consumes. So a mutation becomes a transaction:

    redfish_ctl bios-snapshot --from_spec change.json -f rollback.json  # restore point
    redfish_ctl bios-change   --from_spec change.json   on-reset -r     # apply
    redfish_ctl bios-change   --from_spec rollback.json on-reset -r     # roll back

Scope the snapshot to just the attributes you are changing (``--from_spec`` or
``--attr_name``) so the restore point is a precise inverse, not a 300-attribute
dump. Vendor-neutral: it reads the standard ``Bios.Attributes`` off the host
system, so it works on Dell, HPE iLO, Supermicro, etc.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..cmd_utils import from_json_spec, save_if_needed
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult


class BiosSnapshot(RedfishManagerBase,
                   scm_type=ApiRequestType.BiosSnapshot,
                   name='bios_snapshot',
                   metaclass=Singleton):
    """Capture current BIOS attribute values as a re-applicable restore point."""

    def __init__(self, *args, **kwargs):
        """Construct the bios-snapshot command, forwarding credentials to the base manager."""
        super(BiosSnapshot, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``bios-snapshot`` subcommand and its scoping flags.

        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            '--attr_name', required=False, dest='attr_name', type=str, default=None,
            help="comma-separated attribute names to snapshot (default: all)")
        cmd_parser.add_argument(
            '--from_spec', required=False, dest='from_spec', type=str, default=None,
            help="snapshot only the attributes named in this bios-change spec "
                 "(produces the precise inverse / rollback of that change)")
        return cmd_parser, "bios-snapshot", "command capture BIOS attributes as a rollback restore point"

    def _current_attributes(self, do_async):
        """Return the host's current Bios.Attributes dict, tolerantly.

        :param do_async: note async will subscribe to an event loop.
        :return: the current ``Bios.Attributes`` dict, or empty on any query error.
        """
        try:
            bios = self.base_query(f"{self.idrac_manage_servers}/Bios",
                                   do_async=do_async).data or {}
        except Exception:
            return {}
        attrs = bios.get("Attributes")
        return attrs if isinstance(attrs, dict) else {}

    def execute(self,
                attr_name: Optional[str] = None,
                from_spec: Optional[str] = None,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Emit the current values of the selected attributes as a rollback spec.

        Selection order: the attribute names in ``from_spec``; else the
        comma-separated ``attr_name`` list; else every current attribute. Only
        attributes actually present on the host are included.

        :param attr_name: comma-separated attribute names to snapshot (default: all).
        :param from_spec: bios-change spec whose attribute names scope the snapshot.
        :param filename: if set, save the response to this file.
        :param data_type: json or xml.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: note async will subscribe to an event loop.
        :param do_expanded: accepted for CLI compatibility; not used by this command.
        :return: CommandResult holding the ``{"Attributes": {...}}`` rollback spec.
        """
        current = self._current_attributes(do_async)

        if from_spec:
            spec = from_json_spec(from_spec)
            wanted = list((spec.get("Attributes") or {}).keys())
        elif attr_name:
            wanted = [a.strip() for a in attr_name.split(",") if a.strip()]
        else:
            wanted = list(current.keys())

        restore = {"Attributes": {k: current[k] for k in wanted if k in current}}
        save_if_needed(filename, restore)
        return CommandResult(restore, None, None, None)
