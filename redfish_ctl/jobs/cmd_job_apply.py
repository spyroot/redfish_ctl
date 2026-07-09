"""iDRAC query chassis services

Command provide option to trigger and apply current pending jobs.
Author Mus spyroot@gmail.com
"""
import logging
import time
from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import IDRAC_API, CliJobTypes, JobState
from ..idrac_shared import IdracApiRespond
from ..idrac_shared import ResetType
from ..idrac_shared import Singleton, ApiRequestType
from ..redfish_manager import CommandResult


class JobApply(IDracManager,
               scm_type=ApiRequestType.JobApply,
               name='job_apply',
               metaclass=Singleton):
    """A command query job_service_query.
    """

    def __init__(self, *args, **kwargs):
        super(JobApply, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command and all optional flags.
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser(is_reboot=True)
        cmd_parser.add_argument(
            'setting', choices=['bios', 'boot-option', 'attribute', 'raid'],
            default="bios",
            help="what setting we apply a change. (bios, raid, etc)"
        )
        cmd_parser.add_argument(
            '-w', '--watch', action='store_true',
            required=False, dest="do_watch",
            default=False,
            help="Will block and wait a job to complete. note if job require reboot, "
                 "pass -r or --reboot, otherwise job will not start."
        )

        help_text = "command apply current pending jobs"
        return cmd_parser, "job-apply", help_text

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                setting: Optional[str] = "bios",
                do_watch: Optional[bool] = True,
                do_reboot: Optional[bool] = False,
                sleep_time: Optional[int] = 15,
                default_reboot_type: Optional[str] = ResetType.PowerCycle.value,
                **kwargs) -> CommandResult:
        """Executes apply job and trigger execution.

        :param default_reboot_type:  a default reset type applied in order clean all scheduled jobs
        :param sleep_time:  a default sleep time, in sync mode we wait to see task moved on
        :param do_async: note async will subscribe to an event loop.
        :param filename: if filename indicate call will save a bios setting to a file.
        :param verbose: enables verbose output
        :param setting: job we apply
        :param do_watch: will watch job progress.
        :param do_reboot: many jobs applied only after boot, watch will just block.
        :param data_type: json or xml
        :return: CommandResult and if filename provide will save to a file.
        """
        target_api = f"{self.idrac_members}/{IDRAC_API.Jobs}"
        bios = f"{self.idrac_manage_servers}/Bios/Settings"
        boot_options = f"{self.idrac_manage_servers}/BootOptions"

        job_type = None
        if setting == "bios":
            pd = {
                "RebootJobType": "ForceReboot",
                "TargetSettingsURI": bios,
                "StartTime": "TIME_NOW",
                "EndTime": "TIME_NA"
            }
            job_type = CliJobTypes.Bios_Config.value
        elif setting == "boot-option":
            pd = {
                "RebootJobType": "ForceReboot",
                "TargetSettingsURI": boot_options,
                "StartTime": "TIME_NOW",
                "EndTime": "TIME_NA"
            }
            job_type = CliJobTypes.Bios_Config.value
        else:
            pd = {}

        scheduled_jobs = self.sync_invoke(
            ApiRequestType.Jobs, "jobs_sources_query",
            filter_scheduled=True,
            job_type=job_type,
            job_ids=True
        )

        if scheduled_jobs.error is not None:
            return scheduled_jobs

        # if we have at least one scheduled job
        rebooted = {}
        _do_watch = False if do_async else do_watch

        for job in scheduled_jobs.data:
            # reboot and wait for completion.
            self.logger.info(
                f"Rebooting a host to apply pending job {job}")

            if job not in rebooted:
                cmd_result = self.reboot(
                    do_watch=_do_watch,
                    default_reboot_type=ResetType.PowerCycle.value
                )
                if cmd_result.error:
                    logging.info("Failed reboot a host")
                    break
                rebooted[job] = True

            # in async case we reboot and will not wait.
            if do_async:
                break

            jb = self.get_job(job)
            if 'JobState' in jb:
                job_state = jb['JobState']
                # we wait for job change to change a state
                if job_state == JobState.Scheduled or job_state == JobState.Scheduling:
                    time.sleep(sleep_time)
                else:
                    self.fetch_task(job)

        cmd_result, api_resp = self.base_post(target_api, pd, do_async=do_async)
        if api_resp == IdracApiRespond.AcceptedTaskGenerated:
            task_id = cmd_result.data['task_id']
            task_state = self.fetch_task(task_id)
            cmd_result.data['task_state'] = task_state
            cmd_result.data['task_id'] = task_id

        return cmd_result
