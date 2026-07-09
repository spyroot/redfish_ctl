"""iDRAC export system config command.

Command provides the option to retrieve firmware setting from iDRAC and serialize
back as caller as JSON, YAML, and XML. In addition, it automatically
registers to the command line ctl tool. Similarly to the rest command caller can save
to a file and consume asynchronously or synchronously.

Author Mus spyroot@gmail.com
"""
import argparse
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidJsonSpec
from ..cmd_utils import from_json_spec
from ..idrac_shared import IdracApiRespond
from ..redfish_shared import RedfishJson
from ..cmd_utils import str2bool
from ..idrac_shared import IdracApiRespond, ResetType
from ..cmd_utils import save_if_needed
from ..cmd_exceptions import InvalidArgument
from ..idrac_manager import IDracManager
from ..idrac_shared import IdracApiRespond, Singleton, ApiRequestType
from ..redfish_manager import CommandResult
from ..idrac_shared import IDRAC_API
from ..idrac_shared import IdracApiRespond


class GetTask(
    IDracManager, scm_type=ApiRequestType.GetTask,
    name='task_query',
    metaclass=Singleton):
    """
    Command get task.
    """

    def __init__(self, *args, **kwargs):
        super(GetTask, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command arguments.
        :param cls:
        :return:
        """
        cmd_parser = argparse.ArgumentParser(add_help=False)
        cmd_parser.add_argument('--async', action='store_true',
                                required=False, dest="do_async",
                                default=False,
                                help="Will create a task and will not wait.")

        cmd_parser.add_argument('-t', '--task_id', required=True, dest="job_id", type=str,
                                default=None, help="Job id. Example JID_744718373591")

        cmd_parser.add_argument('-f', '--filename', required=False, type=str,
                                default="",
                                help="filename if we need to save a respond to a file.")

        help_text = "command watch task progress."
        return cmd_parser, "task-watch", help_text

    def execute(self,
                job_id: str,
                data_type: Optional[str] = "json",
                filename: Optional[str] = None,
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs
                ) -> CommandResult:
        """watch current task.
        :param job_id:
        :param do_async:
        :param data_type:
        :param verbose:
        :param filename: if filename indicate call will save a bios setting to a file.
        :return:
        """

        if verbose:
            self.logger.info(
                f"cmd args data_type: {data_type} "
                f"do_async:{do_async} job_id:{job_id}")
            self.logger.info(f"the rest of args: {kwargs}")

        data = self.sync_invoke(
            ApiRequestType.ChassisQuery,
            "chassis_service_query"
        )

        data = {}
        data = self.fetch_task(job_id)
        return CommandResult(data, None, None, None)
