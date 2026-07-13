"""List every Redfish action this box exposes, with its risk level.

    redfish_ctl actions

Read-only. Walks the service tree (Systems, Managers, Chassis and their key
sub-resources, plus the EventService/CertificateService/UpdateService/
TelemetryService/ComponentIntegrity singletons), runs the same action discovery
the action invoker uses, and reports one row per action: {Resource, Action,
FullType, Target, Level}. ``Level`` comes from the destructiveness policy, so an
operator can see at a glance what is safe to run vs. what needs ``--confirm``.

Navigation is by link/``@odata.id`` with no hardcoded ids, so it inventories the
action surface of any Redfish host.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..base_manager import CommandBase
from ..command_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult
from ..redfish_shared import RedfishApi
from .action_policy import classify


class ActionList(CommandBase,
                 scm_type=ApiRequestType.ActionList,
                 name='action_list',
                 metaclass=Singleton):
    """Inventory every Redfish action target on the box and its risk level."""

    def __init__(self, *args, **kwargs):
        super(ActionList, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``actions`` subcommand (read-only)."""
        cmd_parser = cls.base_parser()
        help_text = "command list every Redfish action this box exposes and its risk level"
        return cmd_parser, "actions", help_text

    @staticmethod
    def _members(data):
        """Return the @odata.id strings from a Redfish collection, tolerantly."""
        if not isinstance(data, dict):
            return []
        return [m["@odata.id"] for m in data.get("Members", [])
                if isinstance(m, dict) and isinstance(m.get("@odata.id"), str)]

    def _get(self, uri, do_async):
        """GET a resource body, returning {} on any failure."""
        try:
            return self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}

    @staticmethod
    def _link(data, key):
        """Return the @odata.id of a single ``{key: {@odata.id}}`` link, or None."""
        link = (data or {}).get(key)
        return link.get("@odata.id") if isinstance(link, dict) else None

    def _resource_uris(self, do_async):
        """Collect the resource URIs whose Actions blocks we inventory.

        Bounded on purpose: the three big collections + their members, each
        manager's VirtualMedia, each system's Bios, and the service-level
        singletons. Deep leaves (per-drive SecureErase) are out of scope for the
        first pass; everything here is link-discovered, never hardcoded ids.
        """
        root = self._get(RedfishApi.Version, do_async)
        uris = []

        for coll_key in ("Systems", "Managers", "Chassis"):
            coll_uri = self._link(root, coll_key)
            if not coll_uri:
                continue
            members = self._members(self._get(coll_uri, do_async))
            uris.extend(members)
            for member_uri in members:
                mdata = self._get(member_uri, do_async)
                # managers expose Insert/Eject on each VirtualMedia device
                vm_uri = self._link(mdata, "VirtualMedia")
                if vm_uri:
                    uris.extend(self._members(self._get(vm_uri, do_async)))
                # systems expose Bios actions (ResetBios/ChangePassword) on Bios
                bios_uri = self._link(mdata, "Bios")
                if bios_uri:
                    uris.append(bios_uri)

        for singleton in ("EventService", "CertificateService",
                          "UpdateService", "TelemetryService"):
            s_uri = self._link(root, singleton)
            if s_uri:
                uris.append(s_uri)

        ci_uri = self._link(root, "ComponentIntegrity")
        if ci_uri:
            uris.extend(self._members(self._get(ci_uri, do_async)))

        # de-dup while preserving order
        seen = set()
        return [u for u in uris if not (u in seen or seen.add(u))]

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Walk the tree and report each discovered action + its risk level."""
        rows = []
        for uri in self._resource_uris(do_async):
            data = self._get(uri, do_async)
            # collision-proof: enumerate every #Type.Action from the raw Actions
            # block, so two actions sharing a short name (e.g. ComputerSystem.Reset
            # vs an Oem Contoso.Reset) are both listed, not collapsed.
            for full, target in self._flatten_action_targets(data).items():
                short = full.split(".")[-1] if "." in full else full.lstrip("#")
                rows.append({
                    "Resource": uri,
                    "Action": short,
                    "FullType": full,
                    "Target": target,
                    "Level": classify(full).value,
                })
        return CommandResult(rows, None, None, None)
