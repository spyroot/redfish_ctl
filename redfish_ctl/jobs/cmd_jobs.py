"""iDRAC list of jobs.
 source from iDRAC
Command provides the option to retrieve list of jobs and serialize
back as caller as JSON, YAML, and XML. In addition, it automatically
registers to the command line ctl tool. Similarly to the rest command
caller can save to a file and consume asynchronously or synchronously.

Example.

List of scheduled jobs

redfish_ctl.py jobs --scheduled

Author Mus spyroot@gmail.com
"""
import argparse
from abc import abstractmethod
from datetime import datetime
from typing import Optional

from ..cmd_exceptions import InvalidArgumentFormat
from ..idrac_manager import IDracManager
from ..idrac_shared import Singleton, ApiRequestType
from ..redfish_manager import CommandResult


class JobList(IDracManager,
              scm_type=ApiRequestType.Jobs,
              name='jobs_sources_query',
              metaclass=Singleton):
    """Command handler for list of jobs
    """

    def __init__(self, *args, **kwargs):
        super(JobList, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command and all optional flags.
        :param cls:
        :return:
        """
        cmd_parser = argparse.ArgumentParser(add_help=False)
        cmd_parser.add_argument(
            '--async', action='store_true',
            required=False, dest="do_async",
            default=False,
            help="Will create a task and will not wait."
        )

        cmd_parser.add_argument(
            '-e', '--expanded', action='store_true',
            required=False, dest="do_expanded",
            default=False,
            help="expanded request for deeper view."
        )

        cmd_parser.add_argument(
            '-f', '--filename', required=False, type=str,
            default="",
            help="filename if we need to save a respond to a file."
        )

        cmd_parser.add_argument(
            '--scheduled', action='store_true',
            required=False, dest="filter_scheduled",
            default=False,
            help="return only scheduled."
        )

        cmd_parser.add_argument(
            '--completed', action='store_true',
            required=False, dest="filter_completed",
            default=False,
            help="return only completed."
        )

        cmd_parser.add_argument(
            '--reboot_completed', action='store_true',
            required=False, dest="reboot_completed",
            default=False,
            help="returns only completed after reboot."
        )

        cmd_parser.add_argument(
            '--running', action='store_true',
            required=False, dest="running",
            default=False,
            help="returns only completed after reboot."
        )

        cmd_parser.add_argument(
            '--reboot_pending', action='store_true',
            required=False, dest="reboot_pending",
            default=False,
            help="returns only reboot pending jobs."
        )

        cmd_parser.add_argument(
            '--failed', action='store_true',
            required=False, dest="failed",
            default=False,
            help="returns only failed jobs."
        )

        cmd_parser.add_argument(
            '--sort_by_time', action='store_true',
            required=False, dest="sort_by_time",
            default=True,
            help="Sort jobs by time. First entry the last job."
        )

        cmd_parser.add_argument(
            '--job_type', required=False,
            default="", type=str,
            help="filter by job type. (Example bios_config, firmware_update)"
        )

        cmd_parser.add_argument(
            '--job_ids', required=False, action='store_true',
            default=False,
            help="will return only list of ids"
        )

        # RebootCompleted
        help_text = "command fetch a list of jobs"
        return cmd_parser, "jobs", help_text

    @staticmethod
    def resolve_job_type(jb_type: str):
        """
        """
        if jb_type == "bios_config":
            return "BIOSConfiguration"
        elif jb_type == "firmware_update":
            return "FirmwareUpdate"
        elif jb_type == "reboot_no_force":
            return "RebootNoForce"
        else:
            raise ValueError("Unknown job type")

    def execute(self,
                filename: [str] = None,
                filter_scheduled: Optional[bool] = False,
                filter_completed: Optional[bool] = False,
                reboot_completed: Optional[bool] = False,
                reboot_pending: Optional[bool] = False,
                failed: Optional[bool] = False,
                running: Optional[bool] = False,
                sort_by_time: Optional[bool] = True,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                job_type: Optional[str] = "",
                job_ids: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Command return list idrac jobs

        :param failed: retrieve failed jobs
        :param sort_by_time:  sort by start time
        :param reboot_pending: retrieve jobs reboot pending status
        :param running: retrieve running jobs
        :param reboot_completed: retrieve completed jobs
        :param filter_scheduled: retrieve scheduled jobs
        :param filter_completed:  retrieve completed
        :param do_async: make async request.
        :param verbose: enable verbose
        :param filename: if filename indicate call will save a bios setting to a file.
        :param data_type: json or xml
        :param do_expanded: returns expanded result for API call
        :param job_type: filter on job_type
        :param job_ids: filter by job id only.
        :return: CommandResult and if filename provide will save to a file.
        """
        headers = {}
        if data_type == "json":
            headers.update(self.json_content_type)

        r = f"https://{self.idrac_ip}{self.idrac_members}" \
            f"/Jobs?$expand=*($levels=1)"

        response = self.api_get_call(r, headers)
        data = response.json()
        self.default_error_handler(response)
        filtered_data = []

        members = data.get('Members', [])

        def by_state(*states):
            return [job for job in members if job.get('JobState') in states]

        if filter_scheduled:
            filtered_data += by_state('Scheduled', 'Scheduling')
        if filter_completed:
            filtered_data += by_state('Completed')
        if reboot_completed:
            filtered_data += by_state('RebootCompleted')
        if running:
            filtered_data += by_state('Running')
        if reboot_pending:
            filtered_data += by_state('RebootPending')
        if failed:
            # RebootFailed is a failure state, so it belongs with --failed
            # (previously these were wrongly gated on filter_completed).
            filtered_data += by_state('Failed', 'RebootFailed')
        # default
        if filter_scheduled is False and filter_completed \
                is False and reboot_completed is False and running is False and reboot_pending is False:
            filtered_data = data

        if sort_by_time:
            if isinstance(filtered_data, dict) and 'Members' in filtered_data:
                member_data = filtered_data['Members']
            elif isinstance(filtered_data, list):
                member_data = filtered_data
            else:
                member_data = [filtered_data]
            # sort
            filtered_data = sorted(
                member_data, reverse=True, key=lambda
                    x: datetime.fromisoformat(x['StartTime']).timestamp()
                if 'ActualRunningStartTime' in x else None
            )

        if job_type is not None and len(job_type) > 0:
            if job_type in self._cli_job_type_mapping:
                idrac_job_type = self._cli_job_type_mapping[job_type]
            else:
                supported = self._cli_job_type_mapping.keys()
                # supported = supported
                raise InvalidArgumentFormat(f"{job_type} unknown job type, supported {supported}")

            if isinstance(filtered_data, list):
                filtered_data = [d for d in filtered_data
                                 if 'JobType' in d and d['JobType'] == idrac_job_type
                                 ]

        if job_ids:
            filtered_data = [f["Id"] for f in filtered_data if "Id" in f]

        return CommandResult(filtered_data, None, None, None)
