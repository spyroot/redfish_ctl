"""Infer what a host will boot and its OS-deployment posture.

    redfish_ctl boot-state

Synthesizes the boot/OS state from standard Redfish: the host ``System.Boot``
(mode, override, order), its ``BootOptions`` (what is bootable), and mounted
``VirtualMedia`` — and infers the NEXT boot target. Answers "what will this box
boot, and is anything staged?" without opening the console.

Returns {System, BootMode, Override, OverrideTarget, OneTimeBootPending,
NextBoot, BootOrder, BootableEntries, MountedMedia}. Navigation is by
link/``@odata.id`` with no hardcoded ids, so it works on Dell, HPE iLO,
Supermicro, etc.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult


class BootState(RedfishManagerBase,
                scm_type=ApiRequestType.BootState,
                name='boot-state',
                metaclass=Singleton):
    """Infer the host's next boot target and OS-deployment posture."""

    def __init__(self, *args, **kwargs):
        """Initialize the boot-state command."""
        super(BootState, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``boot-state`` subcommand (read-only).

        :param cls: the CLI base class providing the shared parser factory.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        help_text = "command infer what the host will boot (target/order/media)"
        return cmd_parser, "boot-state", help_text

    @staticmethod
    def _members(data):
        """Return the @odata.id strings from a Redfish collection, tolerantly.

        :param data: a Redfish collection body (a dict is expected).
        :return: list of member @odata.id strings; empty when data is not a dict.
        """
        if not isinstance(data, dict):
            return []
        return [m["@odata.id"] for m in data.get("Members", [])
                if isinstance(m, dict) and isinstance(m.get("@odata.id"), str)]

    def _get(self, uri, do_async):
        """GET a resource body, returning {} on any failure.

        :param uri: Redfish resource path to GET.
        :param do_async: note async will subscribe to an event loop.
        :return: the resource body as a dict, or {} on any failure.
        """
        try:
            return self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}

    def _boot_options(self, system_uri, do_async):
        """Map each BootOption reference to its display name + enabled flag.

        :param system_uri: the host System resource path.
        :param do_async: note async will subscribe to an event loop.
        :return: dict mapping BootOptionReference to {DisplayName, Enabled}.
        """
        options = {}
        coll = self._get(f"{system_uri}/BootOptions", do_async)
        for opt_uri in self._members(coll):
            opt = self._get(opt_uri, do_async)
            ref = opt.get("BootOptionReference") or opt.get("Id") or opt_uri.rsplit("/", 1)[-1]
            options[ref] = {
                "DisplayName": opt.get("DisplayName"),
                "Enabled": opt.get("BootOptionEnabled"),
            }
        return options

    def _mounted_media(self, do_async):
        """List VirtualMedia devices that currently have media inserted.

        :param do_async: note async will subscribe to an event loop.
        :return: list of {Device, Image} for VirtualMedia with media inserted.
        """
        media = []
        vm_uri = self.discover_virtual_media_uri(do_async=do_async)
        for dev_uri in self._members(self._get(vm_uri, do_async)):
            dev = self._get(dev_uri, do_async)
            if dev.get("Inserted"):
                media.append({"Device": dev.get("Id") or dev_uri.rsplit("/", 1)[-1],
                              "Image": dev.get("Image")})
        return media

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Read Boot/BootOptions/VirtualMedia and infer the next boot target.

        :param do_async: note async will subscribe to an event loop.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_expanded: accepted for CLI compatibility; not used by this command.
        :return: CommandResult wrapping the inferred boot state (System, BootMode,
                 Override, OverrideTarget, OneTimeBootPending, NextBoot, BootOrder,
                 BootableEntries, MountedMedia).
        """
        system_uri = self.idrac_manage_servers
        boot = self._get(system_uri, do_async).get("Boot") or {}
        override = boot.get("BootSourceOverrideEnabled")
        target = boot.get("BootSourceOverrideTarget")
        order = boot.get("BootOrder") or []
        options = self._boot_options(system_uri, do_async)

        def label(ref):
            """Return a boot option's display name, falling back to the ref.

            :param ref: a boot option reference key.
            :return: the option's DisplayName, or ref when unknown.
            """
            return (options.get(ref) or {}).get("DisplayName") or ref

        # An active override (Once/Continuous) wins; otherwise it's BootOrder[0].
        if override and override != "Disabled" and target:
            next_boot = target
        elif order:
            next_boot = label(order[0])
        else:
            next_boot = None

        result = {
            "System": system_uri.rsplit("/", 1)[-1],
            "BootMode": boot.get("BootSourceOverrideMode"),
            "Override": override,
            "OverrideTarget": target,
            "OneTimeBootPending": override == "Once",
            "NextBoot": next_boot,
            "BootOrder": [label(r) for r in order],
            "BootableEntries": [{"Ref": r, **v} for r, v in options.items()],
            "MountedMedia": self._mounted_media(do_async),
        }
        return CommandResult(result, None, None, None)
