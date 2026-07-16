"""Reset BIOS settings to defaults through the Redfish BIOS resource's ResetBios action.

    redfish_ctl bios-reset              # dry-run preview
    redfish_ctl bios-reset --confirm    # POST the ResetBios action

The command discovers the host ComputerSystem's ``Bios`` resource and invokes
its own ``#Bios.ResetBios`` action. Resetting BIOS settings rewrites platform
configuration, so the shared action guard treats it as DESTRUCTIVE and previews
by default unless ``--confirm`` is present.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton
from ..redfish_shared import RedfishApi

_RESET_BIOS_ACTION = "#Bios.ResetBios"


class BiosResetDefault(RedfishManagerBase,
                       scm_type=ApiRequestType.BiosResetDefault,
                       name="bios_reset",
                       metaclass=Singleton):
    """Reset the host BIOS resource through the discovered ResetBios action."""

    def __init__(self, *args, **kwargs):
        """Initialize the bios-reset command."""
        super(BiosResetDefault, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded ``bios-reset`` subcommand.

        :param cls: the owning command class, used to build the base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            dest="confirm",
            default=False,
            help="fire the ResetBios POST; without it the command only previews",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="resolve the target and show it without POSTing",
        )
        return cmd_parser, "bios-reset", "command reset BIOS settings to defaults"

    def _bios_uri(self, do_async: bool) -> str:
        """Resolve the host BIOS resource URI from the ComputerSystem link.

        :param do_async: issue the host-system query over the async path when True.
        :return: the linked BIOS resource URI, or the standard ``/Bios`` fallback.
        """
        system_uri = self.idrac_manage_servers
        system = self.base_query(system_uri, do_async=do_async).data or {}
        bios_link = system.get("Bios") if isinstance(system, dict) else None
        if isinstance(bios_link, dict) and bios_link.get("@odata.id"):
            return bios_link["@odata.id"]
        return self._bios_fallback_uri(system_uri)

    @staticmethod
    def _bios_fallback_uri(system_uri: str) -> str:
        """Build the conventional BIOS URI under a ComputerSystem URI.

        :param system_uri: the ComputerSystem resource URI to base the path on.
        :return: the conventional ``<system_uri>/Bios`` resource URI.
        """
        return f"{system_uri.rstrip('/')}/{str(RedfishApi.Bios).strip('/')}"

    def execute(self,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Resolve and invoke ``Bios.ResetBios`` on the host BIOS resource.

        :param confirm: authorize the DESTRUCTIVE ResetBios POST to run.
        :param dry_run: force a dry-run preview even when ``confirm`` is true.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue the underlying query and POST on the async path.
        :return: a CommandResult with a dry-run preview or POST result.
        """
        return self.invoke_action(
            self._bios_uri(bool(do_async)),
            "ResetBios",
            payload={},
            full_action_type=_RESET_BIOS_ACTION,
            do_async=do_async,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
        )
