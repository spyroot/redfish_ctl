"""iDRAC clear pending values.

    redfish_ctl attr-clear-pending

Command provides the option to clear all the pending attributes values.

Author Mus spyroot@gmail.com
"""
import argparse
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import FailedDiscoverAction
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import RedfishApiRespond
from ..redfish_manager_shared import Singleton, ApiRequestType
from ..redfish_manager import CommandResult
from ..redfish_shared import RedfishJson


class AttributeClearPending(
    RedfishManagerBase,
    scm_type=ApiRequestType.AttributeClearPending,
    name='clear_pending',
    metaclass=Singleton):
    """
    This cmd action is used to clear all the pending
    values currently in iDRAC.
    """

    def __init__(self, *args, **kwargs):
        """Initialize the attr-clear-pending command."""
        super(AttributeClearPending, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``attr-clear-pending`` subcommand.

        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = argparse.ArgumentParser(add_help=False)

        cmd_parser.add_argument(
            '--async', default=False, required=False,
            action='store_true', dest="do_async",
            help="Will use asyncio.")

        help_text = "command clear attribute pending values"
        return cmd_parser, "attr-clear-pending", help_text

    def execute(self,
                do_async: Optional[bool] = False,
                data_type: Optional[str] = "json",
                **kwargs
                ) -> CommandResult:
        """Execute clear pending command.

        Discovers the ``#DellManager.ClearPending`` action from a deep
        attribute query and POSTs it.

        :param do_async: if set, submit the clear-pending action asynchronously.
        :param data_type: response content type, json or xml.
        :return: CommandResult from the ClearPending POST, augmented with
            task_state and task_id when the BMC generates a task; the attribute
            query result is returned unchanged when that lookup errors.
        :raises FailedDiscoverAction: when the ClearPending action target cannot
            be discovered.
        """
        headers = {}
        if data_type == "json":
            headers.update(self.json_content_type)

        target = None
        attributes_cmd = self.sync_invoke(
            ApiRequestType.AttributesQuery,
            "attribute_inventory", do_deep=True
        )
        if attributes_cmd.error is not None:
            return attributes_cmd

        if isinstance(attributes_cmd.extra, list):
            for extra in attributes_cmd.extra:
                if RedfishJson.Actions in extra:
                    actions = extra[RedfishJson.Actions]
                    if '#DellManager.ClearPending' in actions:
                        target = actions['#DellManager.ClearPending']['target']

        if target is None:
            raise FailedDiscoverAction(
                "Failed discover clear pending attribute action."
            )

        cmd_result, api_resp = self.base_post(target, do_async=do_async)
        if api_resp == RedfishApiRespond.AcceptedTaskGenerated:
            task_id = cmd_result.data['task_id']
            task_state = self.fetch_task(task_id)
            cmd_result.data['task_state'] = task_state
            cmd_result.data['task_id'] = task_id

        return cmd_result
