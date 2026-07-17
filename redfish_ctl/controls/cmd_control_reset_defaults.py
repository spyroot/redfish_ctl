"""Reset Redfish Control resources to their default setpoints.

    redfish_ctl control-reset-defaults
    redfish_ctl control-reset-defaults --control HGX_GPU_0/ClockLimit_0
    redfish_ctl control-reset-defaults --control HGX_GPU_0/ClockLimit_0 --confirm

The command discovers ``#Control.ResetToDefaults`` from each Control resource's
own ``Actions`` block. Resetting a Control rewrites a BMC-managed control value,
so the action is DESTRUCTIVE: without ``--confirm`` the command only previews
the POST.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import REDFISH_API, ApiRequestType, Singleton

_CONTROL_RESET_ACTION = "#Control.ResetToDefaults"


class ControlResetDefaults(RedfishManagerBase,
                           scm_type=ApiRequestType.ControlResetDefaults,
                           name="control-reset-defaults",
                           metaclass=Singleton):
    """Discover and reset Redfish Control resources to default values."""

    def __init__(self, *args, **kwargs):
        """Initialize the control-reset-defaults command."""
        super(ControlResetDefaults, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded ``control-reset-defaults`` subcommand.

        :param cls: command class supplying the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--control",
            required=False,
            dest="control",
            default=None,
            help="Control Id, Chassis/Id selector, Control URI, or action URI; "
                 "omit to list reset-capable controls",
        )
        cmd_parser.add_argument(
            "--chassis",
            required=False,
            dest="chassis",
            default=None,
            help="chassis Id used to disambiguate duplicate Control ids",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            dest="confirm",
            default=False,
            help="fire the Control.ResetToDefaults POST; without it the "
                 "command previews",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="resolve the target and show it without POSTing; overrides --confirm",
        )
        return (
            cmd_parser,
            "control-reset-defaults",
            "command reset a Redfish Control resource to defaults",
        )

    @staticmethod
    def _members(data):
        """Extract member ``@odata.id`` links from a Redfish collection.

        :param data: collection payload expected to hold a ``Members`` array.
        :return: list of member ``@odata.id`` strings; empty when malformed.
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
        """Return the ``@odata.id`` of a linked resource under ``key``.

        :param data: resource payload to read the link from.
        :param key: name of the property holding the linked resource object.
        :return: linked ``@odata.id`` string, or None when absent.
        """
        value = data.get(key) if isinstance(data, dict) else None
        if isinstance(value, dict) and isinstance(value.get("@odata.id"), str):
            return value["@odata.id"]
        return None

    @staticmethod
    def _chassis_id(chassis_uri):
        """Derive a chassis identifier from a chassis URI.

        :param chassis_uri: chassis ``@odata.id`` path.
        :return: the last path segment of the URI.
        """
        return chassis_uri.rstrip("/").rsplit("/", 1)[-1]

    @staticmethod
    def _action_target(control):
        """Return the Control.ResetToDefaults target from a Control body.

        :param control: decoded Control resource body.
        :return: action target URI, or None when the action is absent.
        """
        actions = control.get("Actions") if isinstance(control, dict) else None
        action = (
            actions.get(_CONTROL_RESET_ACTION)
            if isinstance(actions, dict) else None
        )
        if not isinstance(action, dict):
            return None
        target = action.get("target")
        return target if isinstance(target, str) and target else None

    def _query_optional(self, uri, do_async=False):
        """Query a URI, returning an empty dict instead of raising.

        :param uri: Redfish resource URI to query.
        :param do_async: when True, issue the query asynchronously.
        :return: response data dict, or an empty dict on any read error.
        """
        try:
            data = self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _target_row(chassis_id, member_uri, control):
        """Build one reset-capable Control target row.

        :param chassis_id: chassis identifier that owns the Control collection.
        :param member_uri: Control resource URI read from the collection.
        :param control: decoded Control resource body.
        :return: public row describing the reset-capable Control.
        """
        uri = control.get("@odata.id", member_uri)
        return {
            "Chassis": chassis_id,
            "Id": control.get("Id") or uri.rstrip("/").rsplit("/", 1)[-1],
            "Name": control.get("Name"),
            "ControlType": control.get("ControlType"),
            "ControlMode": control.get("ControlMode"),
            "SetPoint": control.get("SetPoint"),
            "SetPointUnits": control.get("SetPointUnits"),
            "DefaultSetPoint": control.get("DefaultSetPoint"),
            "Uri": uri,
            "Target": ControlResetDefaults._action_target(control),
        }

    def _discover_targets(self, do_async=False):
        """Discover Controls that advertise Control.ResetToDefaults.

        :param do_async: when True, issue Redfish queries asynchronously.
        :return: list of reset-capable Control target rows.
        """
        chassis = self.base_query(REDFISH_API.Chassis, do_async=do_async)
        rows = []
        for chassis_uri in self._members(chassis.data):
            chassis_id = self._chassis_id(chassis_uri)
            chassis_data = self._query_optional(chassis_uri, do_async=do_async)
            controls_uri = self._link(chassis_data, "Controls")
            if not controls_uri:
                continue
            collection = self._query_optional(controls_uri, do_async=do_async)
            for member_uri in self._members(collection):
                control = self._query_optional(member_uri, do_async=do_async)
                if not self._action_target(control):
                    continue
                rows.append(self._target_row(chassis_id, member_uri, control))
        return rows

    @staticmethod
    def _matches(rows, control, chassis=None):
        """Filter reset-capable rows by selector and optional chassis.

        :param rows: discovered reset-capable target rows.
        :param control: Control Id, ``Chassis/Id``, resource URI, or action URI.
        :param chassis: optional chassis Id to disambiguate duplicate Ids.
        :return: list of matching rows.
        """
        wanted = (control or "").strip()
        if not wanted:
            raise InvalidArgument("control selector cannot be empty")

        if wanted.startswith("/redfish/"):
            matches = [
                row for row in rows
                if wanted in {row["Uri"], row["Target"]}
            ]
        elif "/" in wanted:
            chassis_id, control_id = wanted.split("/", 1)
            matches = [
                row for row in rows
                if row["Chassis"].lower() == chassis_id.lower()
                and str(row["Id"]).lower() == control_id.lower()
            ]
        else:
            matches = [
                row for row in rows
                if str(row["Id"]).lower() == wanted.lower()
            ]

        if chassis:
            folded_chassis = chassis.strip().lower()
            matches = [
                row for row in matches
                if row["Chassis"].lower() == folded_chassis
            ]
        return matches

    def execute(self,
                control: Optional[str] = None,
                chassis: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List or reset Redfish Control resources to default values.

        With no ``--control`` selector the command lists every Control resource
        that advertises ``#Control.ResetToDefaults`` and does not POST. With a
        selector it invokes the discovered action; because the action is
        DESTRUCTIVE, the POST only fires with ``--confirm``. ``--dry_run`` remains
        a no-POST override even when ``--confirm`` is also set.

        :param control: Control Id, ``Chassis/Id``, resource URI, or action URI.
        :param chassis: optional chassis Id to disambiguate duplicate controls.
        :param confirm: authorize the Control.ResetToDefaults POST to fire.
        :param dry_run: resolve the target and show the payload without POSTing.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue the underlying query and POST on the async path.
        :return: CommandResult with targets, preview, execution result, or error.
        """
        rows = self._discover_targets(do_async=bool(do_async))
        if control is None:
            return CommandResult({"control_reset_targets": rows}, None, None, None)

        matches = self._matches(rows, control, chassis=chassis)
        if not matches:
            return CommandResult(
                {"available": rows},
                None,
                None,
                f"Control.ResetToDefaults target not found: {control}",
            )
        if len(matches) > 1:
            return CommandResult(
                {"matches": matches},
                None,
                None,
                "multiple Control.ResetToDefaults targets found; "
                "pass --chassis or a full --control URI",
            )

        row = matches[0]
        return self.invoke_action(
            row["Uri"],
            "ResetToDefaults",
            payload={},
            full_action_type=_CONTROL_RESET_ACTION,
            do_async=do_async,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
        )
