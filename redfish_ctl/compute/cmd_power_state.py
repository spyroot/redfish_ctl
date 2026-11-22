"""Reset the power state for a compute system command.

    redfish_ctl reboot --reset_type GracefulRestart

This action is used to reset the system.
Command provides the option to reboot, and change power state.

Author Mus spyroot@gmail.com
"""
import argparse
from abc import abstractmethod
from typing import Optional

from ..redfish_api_common import ApiRequestType, Singleton
from ..redfish_manager import CommandResult, RedfishManager


class RebootHost(RedfishManager,
                 scm_type=ApiRequestType.ComputerSystemReset,
                 name='reboot',
                 metaclass=Singleton):
    """
    "Actions": {
        "#ComputerSystem.Reset": {
            "ResetType@Redfish.AllowableValues": [
                "On",
                "ForceOff",
                "ForceRestart",
                "GracefulRestart",
                "GracefulShutdown",
                "PushPowerButton",
                "Nmi",
                "PowerCycle"
            ],
            "target": "/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset"
        }
    },
    """

    def __init__(self, *args, **kwargs):
        """Initialize the reboot command."""
        super(RebootHost, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the reboot subcommand.

        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = argparse.ArgumentParser(add_help=False)
        cmd_parser.add_argument(
            '--reset_type',
            required=False, dest='reset_type',
            default="GracefulRestart", type=str,
            help="Reset On, ForceOff, "
                 "ForceRestart, GracefulRestart, "
                 "GracefulShutdown, "
                 "PushPowerButton, Nmi, PowerCycle.")

        cmd_parser.add_argument(
            '-a', '--async', action='store_true',
            required=False, dest="do_async",
            default=False, help="will use async call.")

        cmd_parser.add_argument(
            '-w', '--wait', action='store_true',
            required=False, dest="do_wait",
            default=False, help="wait for reboot.")

        cmd_parser.add_argument(
            '--dry_run', action='store_true',
            required=False, dest="dry_run",
            default=False,
            help="preview the resolved reset target + payload; POST nothing.")

        help_text = "reboots the system"
        return cmd_parser, "reboot", help_text

    def wait_for_reboot(self, sleep_time, max_retry):
        """Wait for the BMC to go down and return through the shared wait command.

        :param sleep_time: seconds between reachability polls.
        :param max_retry: maximum number of polling intervals.
        :return: wait command result for the complete reboot cycle.
        """
        return self.sync_invoke(
            ApiRequestType.WaitReady,
            "wait",
            wait_timeout=float(sleep_time) * int(max_retry),
            wait_interval=float(sleep_time),
            wait_reboot_cycle=True,
        )

    def execute(self,
                filename: Optional[str] = "",
                data_type: Optional[str] = "json",
                reset_type: Optional[str] = "On",
                do_async: Optional[bool] = False,
                do_wait: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                sleep_time: Optional[int] = 10,
                max_retry: Optional[int] = 10,
                **kwargs
                ) -> CommandResult:
        """Reboot the host by resetting its ComputerSystem.

        The reset target is DISCOVERED from the host ComputerSystem's own
        ``#ComputerSystem.Reset`` action (vendor-neutral: works whatever the
        system id is -- ``System.Embedded.1``, ``System_0``, ...), never a
        hardcoded path. The POST goes through :meth:`invoke_action`, which
        carries the shared destructiveness guard.

        ``reboot`` is an explicit reboot request, so it confirms by default and
        actually fires; pass ``--dry_run`` to preview the resolved target +
        payload without POSTing anything. See the guarded ``system-reset``
        command for a reset that previews unless ``--confirm`` is given.

        :param do_wait: wait for the reboot job to complete.
        :param do_async: issue the request on the asyncio path.
        :param dry_run: preview the resolved target + payload, POST nothing.
        :param reset_type: "On, ForceOff, ForceRestart, GracefulRestart,
                           GracefulShutdown, PushPowerButton, Nmi, PowerCycle"
        :param sleep_time: wait for the reboot job to start.
        :param max_retry: maximum retry while waiting for the reboot job.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param kwargs:
        :return: CommandResult; on a real fire ``.data`` carries the task id/state,
                 on a dry-run it carries the resolved target + payload.
        """
        self.logger.info(f"issuing reset request ResetType={reset_type}")

        cmd_result = self.invoke_action(
            self.managed_system_uri,
            "Reset",
            payload={"ResetType": reset_type},
            full_action_type="#ComputerSystem.Reset",
            do_async=do_async,
            expected_status=202,
            dry_run=bool(dry_run),
            confirm=True,
        )

        data = cmd_result.data if isinstance(cmd_result.data, dict) else {}
        fired = cmd_result.error is None and not data.get("dry_run")

        # A real fire returns a Redfish task; surface its state like before.
        if fired and data.get("task_id"):
            task_id = data["task_id"]
            self.logger.info(f"received task id {task_id}, fetch task state")
            data["task_state"] = self.fetch_task(task_id)
            data["task_id"] = task_id

        if do_wait and fired:
            wait_result = self.wait_for_reboot(sleep_time, max_retry)
            data["wait"] = wait_result.data
            if wait_result.error is not None:
                return CommandResult(
                    data,
                    cmd_result.discovered,
                    cmd_result.extra,
                    wait_result.error,
                )

        return cmd_result
