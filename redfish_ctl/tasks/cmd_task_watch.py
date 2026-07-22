"""Task watch command.

Command provides the option to retrieve firmware setting from a Redfish endpoint and serialize
back as caller as JSON, YAML, and XML. In addition, it automatically
registers to the command line ctl tool. Similarly to the rest command caller can save
to a file and consume asynchronously or synchronously.

Example::

    redfish_ctl task-watch --task_id JID_744718373591

Author Mus spyroot@gmail.com
"""
import argparse
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidArgument, InvalidJsonSpec
from ..cmd_utils import from_json_spec, save_if_needed, str2bool
from ..idrac_manager import IDracManager
from ..idrac_shared import REDFISH_API, ApiRequestType, RedfishApiRespond, ResetType, Singleton
from ..redfish_manager import CommandResult
from ..redfish_shared import RedfishJson


class GetTask(
    IDracManager, scm_type=ApiRequestType.GetTask,
    name='task_query',
    metaclass=Singleton):
    """
    Command get task.
    """

    def __init__(self, *args, **kwargs):
        """Initialize the task-watch command."""
        super(GetTask, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the task-watch subcommand and its arguments.

        :return: tuple of (ArgumentParser, command name, command help).
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
        """Fetch the current state of a task by its job id.

        :param job_id: task/job id to fetch (e.g. JID_744718373591).
        :param data_type: accepted for CLI compatibility; only echoed in verbose
            logging, not used by this command.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param verbose: enables verbose logging of the received arguments.
        :param do_async: accepted for CLI compatibility; only echoed in verbose
            logging, not used by this command.
        :return: CommandResult wrapping the fetched task payload.
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
