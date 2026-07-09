"""iDRAC delete job from iDRAC action

Command provides the option to delete a job from iDRAC.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import IdracApiRespond
from ..idrac_shared import Singleton, ApiRequestType
from ..redfish_manager import CommandResult


class JobDel(IDracManager,
             scm_type=ApiRequestType.JobDel,
             name='job_del',
             metaclass=Singleton):
    """Command gets a job from iDRAC
    """

    def __init__(self, *args, **kwargs):
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

        python idrac_ctl.py del_job --job_id RID_744980379189

        :param job_id: iDRAC job_id JID_744718373591
        :param do_async: note async will subscribe to an event loop.
        :param verbose: enables verbose output
        :param filename: if filename indicate call will save a bios setting to a file.
        :param data_type: json or xml
        :return: CommandResult and if filename provide will save to a file.
        """
        req = f"{self.idrac_members}/Oem/Dell/Jobs/{job_id}"
        self.logger.info(f"Sending request to {req}")
        cmd_result, api_resp = self.base_delete(
            req, payload={},
            do_async=do_async
        )

        if api_resp == IdracApiRespond.AcceptedTaskGenerated:
            task_id = cmd_result.data['task_id']
            task_state = self.fetch_task(task_id)
            cmd_result.data['task_state'] = task_state
            cmd_result.data['task_id'] = task_id

        return cmd_result
