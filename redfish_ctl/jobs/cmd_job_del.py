"""iDRAC delete job from iDRAC action

Command provides the option to delete a job from iDRAC.

Example::

    redfish_ctl job-rm --job_id JID_744718373591

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, RedfishApiRespond, Singleton
from ..redfish_manager import CommandResult


class JobDel(IDracManager,
             scm_type=ApiRequestType.JobDel,
             name='job_del',
             metaclass=Singleton):
    """Command gets a job from iDRAC
    """

    def __init__(self, *args, **kwargs):
        """Initialize the job-rm command."""
        super(JobDel, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command and all optional flags.
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser(is_reboot=False, is_file_save=False)

        cmd_parser.add_argument(
            '-j', '--job_id', required=True, dest="job_id", type=str,
            default=None, help="Job id. Example JID_744718373591")

        help_text = "command deletes an existing job"
        return cmd_parser, "job-rm", help_text

    def execute(self, job_id: str,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Executes delete job from iDRAC action

        python redfish_ctl.py del_job --job_id RID_744980379189

        :param job_id: iDRAC job_id JID_744718373591
        :param do_async: note async will subscribe to an event loop.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :return: CommandResult and if filename provide will save to a file.
        """
        req = f"{self.idrac_members}/Oem/Dell/Jobs/{job_id}"
        self.logger.info(f"Sending request to {req}")
        cmd_result, api_resp = self.base_delete(
            req, payload={},
            do_async=do_async
        )

        if api_resp == RedfishApiRespond.AcceptedTaskGenerated:
            task_id = cmd_result.data['task_id']
            task_state = self.fetch_task(task_id)
            cmd_result.data['task_state'] = task_state
            cmd_result.data['task_id'] = task_id

        return cmd_result
