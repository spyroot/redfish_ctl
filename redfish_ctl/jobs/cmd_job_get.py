"""iDRAC fetch job from iDRAC

Command provides the option to retrieve job information  from iDRAC
and serialize back as caller as JSON, YAML, and XML.

Author Mus spyroot@gmail.com
"""
import argparse
from abc import abstractmethod
from typing import Optional

from ..cmd_utils import save_if_needed
from ..idrac_manager import IDracManager
from ..idrac_shared import Singleton, ApiRequestType
from ..redfish_manager import CommandResult


class JobGet(IDracManager,
             scm_type=ApiRequestType.JobGet,
             name='job_query',
             metaclass=Singleton):
    """Command gets a job from iDRAC
    """

    def __init__(self, *args, **kwargs):
        super(JobGet, self).__init__(*args, **kwargs)

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
            required=False, dest="do_async", default=False,
            help="Will create a task and will not wait."
        )

        cmd_parser.add_argument(
            '-j', '--job_id',
            required=True, dest="job_id", type=str, default=None,
            help="Job id. Example JID_744718373591"
        )

        cmd_parser.add_argument(
            '-f', '--filename',
            required=False, type=str, default="",
            help="filename if we need to save a respond to a file."
        )

        help_text = "command fetch a job"
        return cmd_parser, "job", help_text

    def execute(self, job_id: str,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Query information for particular job.

        python idrac_ctl.py job --job_id JID_744718373591

        :param job_id: iDRAC job_id JID_744718373591
        :param do_async: note async will subscribe to an event loop.
        :param verbose: enables verbose output
        :param filename: if filename indicate call will save a bios setting to a file.
        :param data_type: json or xml
        :return: CommandResult and if filename provide will save to a file.
        """
        data = self.get_job(job_id, do_async=do_async)
        save_if_needed(filename, data)
        return CommandResult(data, None, None, None)
