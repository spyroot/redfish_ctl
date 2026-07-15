"""Attribute update command.

    redfish_ctl attr-update --from_spec attribute.json

Command provides the option to retrieve the Redfish endpoint attribute and serialize
back as caller as JSON, YAML, and XML. In addition, it automatically
registers to the command line ctl tool. Similarly to the rest command caller can save
to a file and consume asynchronously or synchronously.

python redfish_ctl.py --json attribute --filter ServerPwrMon.1.PeakCurrentTime

Author Mus spyroot@gmail.com
"""
import argparse
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidArgumentFormat
from ..cmd_utils import from_json_spec
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import RedfishApiRespond
from ..redfish_manager_shared import Singleton, ApiRequestType
from ..redfish_manager import CommandResult


class AttributesUpdate(
    RedfishManagerBase,
    scm_type=ApiRequestType.AttributesUpdate,
    name='attribute_update',
    metaclass=Singleton):
    """Attribute update command, fetch attribute data, caller can save to a file
    or output to a file or pass downstream.
    """

    def __init__(self, *args, **kwargs):
        """Initialize the attr-update command."""
        super(AttributesUpdate, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``attr-update`` subcommand.

        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_arg = argparse.ArgumentParser(add_help=False)

        cmd_arg.add_argument(
            '--async', action='store_true', required=False, dest="do_async",
            default=False, help="Will create a task and will not wait.")

        cmd_arg.add_argument(
            '-s', '--from_spec',
            help="Read json spec for new bios attributes,  "
                 "(Example --from_spec attribute.json)",
            type=str, required=True, dest="from_spec", metavar="file name",
            default=None
        )

        help_text = "command fetch the attribute view"
        return cmd_arg, "attr-update", help_text

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                from_spec: Optional[str] = "",
                **kwargs) -> CommandResult:
        """Update Redfish endpoint attributes.

        :param from_spec: path to a json spec file holding the attribute
            key/value pairs to apply.
        :param do_async: if set, submit the update asynchronously.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: response content type, json or xml.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :return: CommandResult from the attribute PATCH, augmented with
            task_state and task_id when the BMC generates a task.
        :raises InvalidArgumentFormat: when from_spec is empty.
        :raise: AuthenticationFailed, UnexpectedResponse
        """
        headers = {}
        if data_type == "json":
            headers.update(self.json_content_type)

        if from_spec is None or len(from_spec) == 0:
            raise InvalidArgumentFormat(
                "from_spec is empty string"
            )

        api_target = "/redfish/v1/Managers/System.Embedded.1/Attributes"
        payload = from_json_spec(from_spec)

        cmd_result, api_resp = self.base_patch(
            api_target, payload=payload,
            do_async=do_async
        )

        if api_resp == RedfishApiRespond.AcceptedTaskGenerated:
            task_id = cmd_result.data['task_id']
            task_state = self.fetch_task(task_id)
            cmd_result.data['task_state'] = task_state
            cmd_result.data['task_id'] = task_id

        return cmd_result
