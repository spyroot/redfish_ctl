"""iDRAC reset a power state for compute system command.

    redfish_ctl reboot --reset_type GracefulRestart

This action is used to reset the system.
Command provides the option to reboot, and change power state.

Author Mus spyroot@gmail.com
"""
import argparse
import time
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import MissingResource
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, CliJobTypes, Singleton
from ..redfish_exceptions import RedfishException
from ..redfish_manager import CommandResult


class RebootHost(RedfishManagerBase,
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
        """If we need wait or graceful shutdown.  It will wait for reboot task
        and wait for reboot to complete. It makes sense to call this method
        only if reset already called.

        :param sleep_time: seconds to sleep between reboot-pending polls.
        :param max_retry: maximum retries while waiting for the reboot job.
        :return: the jobs-query CommandResult if that query errors; otherwise
                 None once the reboot-pending wait loop completes.
        """
        _reboot = 1
        retry_counter = 0
        while _reboot != 0:
            if max_retry == 10:
                self.logger.info(
                    "Power state, max retried reached, "
                    "no pending reboot states."
                )
                break

            # get reboot reboot pending tasks
            scheduled_jobs = self.sync_invoke(
                ApiRequestType.Jobs, "jobs_sources_query",
                reboot_pending=True,
                job_type=CliJobTypes.RebootNoForce.value,
                job_ids=True
            )
            if scheduled_jobs.error is not None:
                return scheduled_jobs

            if len(scheduled_jobs.data) == 0:
                time.sleep(sleep_time)

            try:
                for job in scheduled_jobs.data:
                    # reboot and wait for completion.
                    self.logger.info(f"Reboot pending job created: task id {job}")
                    self.fetch_task(job)
                    _reboot -= 1
            except MissingResource as mr:
                self.logger.error(str(mr))
                time.sleep(sleep_time)
            except RedfishException as re:
                self.logger.error(str(re))
                time.sleep(sleep_time)

            self.logger.info(f"Sleeping {sleep_time} sec "
                             f"and waiting for reboot pending")
            time.sleep(sleep_time)
            retry_counter += 1

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
            self.idrac_manage_servers,
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
            self.wait_for_reboot(sleep_time, max_retry)

        return cmd_result
