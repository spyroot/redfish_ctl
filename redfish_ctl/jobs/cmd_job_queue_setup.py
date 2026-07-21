"""Guarded Dell JobService SetupJobQueue action command.

Examples:
    redfish_ctl dell-job-queue-setup
    redfish_ctl dell-job-queue-setup --confirm
"""
from abc import abstractmethod
from typing import Optional

from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton

_DELL_JOB_SERVICE = "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellJobService"
_SETUP_JOB_QUEUE_ACTION = "#DellJobService.SetupJobQueue"


class DellJobQueueSetup(
    RedfishManagerBase,
    scm_type=ApiRequestType.DellJobQueueSetup,
    name="dell-job-queue-setup",
    metaclass=Singleton,
):
    """Run DellJobService.SetupJobQueue behind the action safety gate."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-job-queue-setup command."""
        super(DellJobQueueSetup, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the dell-job-queue-setup command and flags.

        :param cls: the CLI manager class providing the base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            dest="confirm",
            default=False,
            help="actually run SetupJobQueue; otherwise preview only",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="force a preview even when --confirm is present",
        )
        return (
            cmd_parser,
            "dell-job-queue-setup",
            "run Dell JobService SetupJobQueue (guarded)",
        )

    def execute(
            self,
            confirm: Optional[bool] = False,
            dry_run: Optional[bool] = False,
            do_async: Optional[bool] = False,
            **kwargs) -> CommandResult:
        """Run DellJobService.SetupJobQueue, previewing unless confirmed.

        :param confirm: if set (and not dry_run), actually start the setup action.
        :param dry_run: force a preview even when confirm is present.
        :param do_async: note async will subscribe to an event loop.
        :param kwargs: accepted for CLI compatibility; not used by this command.
        :return: CommandResult with the preview or action result.
        """
        return self.invoke_action(
            _DELL_JOB_SERVICE,
            "SetupJobQueue",
            payload={},
            full_action_type=_SETUP_JOB_QUEUE_ACTION,
            do_async=do_async,
            expected_status=202,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
        )
