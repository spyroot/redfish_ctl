"""Reset a host ComputerSystem (ComputerSystem.Reset), vendor-neutral + guarded.

    redfish_ctl system-reset --reset_type GracefulRestart            # dry-run preview
    redfish_ctl system-reset --reset_type GracefulRestart --confirm  # actually reset

Powers/reboots the host via the ComputerSystem's own ``#ComputerSystem.Reset``
action, discovered from the resource (no hardcoded Dell path), so it works on the
host system whatever its id (System_0, System.Embedded.1, ...). The host system is
resolved through ``idrac_manage_servers`` (which prefers the Bios/Boot-bearing
host over a GPU baseboard).

DESTRUCTIVE: this disrupts the running host, so it defaults to a DRY-RUN — it
prints the resolved target + payload and POSTs nothing — until you pass
``--confirm``. The guard is enforced in ``invoke_action``, not here.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult


class SystemReset(IDracManager,
                  scm_type=ApiRequestType.SystemReset,
                  name='system_reset',
                  metaclass=Singleton):
    """Reset the host ComputerSystem via a discovered ComputerSystem.Reset action."""

    def __init__(self, *args, **kwargs):
        """Initialize the system-reset command."""
        super(SystemReset, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``system-reset`` subcommand and its safety flags.

        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            '--reset_type', required=False, dest='reset_type', type=str,
            default="GracefulRestart",
            help="ResetType: On, ForceOff, GracefulShutdown, GracefulRestart, "
                 "ForceRestart, ForceOn, PushPowerButton, Nmi (box-dependent)")
        cmd_parser.add_argument(
            '--confirm', action='store_true', dest='confirm',
            help="actually perform the reset (without it this is a dry-run)")
        cmd_parser.add_argument(
            '--dry_run', action='store_true', dest='dry_run',
            help="force a dry-run preview even if --confirm is given")
        return cmd_parser, "system-reset", "command reset the host system (guarded)"

    def execute(self,
                reset_type: Optional[str] = "GracefulRestart",
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Resolve ComputerSystem.Reset on the host system and POST it (guarded).

        Returns a dry-run preview (target + payload, no POST) unless ``--confirm``
        is given; the destructiveness guard lives in ``invoke_action``.

        :param reset_type: the ComputerSystem ResetType to request (On, ForceOff,
                           GracefulRestart, ForceRestart, ...).
        :param confirm: actually perform the reset; without it this is a dry-run.
        :param dry_run: force a dry-run preview even when ``--confirm`` is given.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue the request on the asyncio path.
        :return: CommandResult; a dry-run preview (resolved target + payload, no
                 POST) unless ``--confirm`` is given, otherwise the reset response.
        """
        return self.invoke_action(
            self.idrac_manage_servers,
            "Reset",
            payload={"ResetType": reset_type},
            full_action_type="#ComputerSystem.Reset",
            do_async=do_async,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
        )
