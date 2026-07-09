"""iDRAC command clear boot options pending values.

Command provides the option to clear boot source pending values
via dell oem.  If boot options change boot order
or boot options flags.

Author Mus spyroot@gmail.com
"""
import argparse
from abc import abstractmethod
from typing import Optional


from ..cmd_utils import save_if_needed
from ..cmd_exceptions import InvalidArgument
from ..idrac_manager import IDracManager
from ..idrac_shared import IdracApiRespond, Singleton, ApiRequestType
from ..redfish_manager import CommandResult
from ..idrac_shared import IDRAC_API
from ..idrac_shared import IdracApiRespond


class BootOptionsClearPending(IDracManager,
                              scm_type=ApiRequestType.BootOptionsClearPending,
                              name='clear_pending',
                              metaclass=Singleton):
    """
    This cmd action is used to clear all BIOS pending
    values currently in iDRAC.
    """

    def __init__(self, *args, **kwargs):
        super(BootOptionsClearPending, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command
        :param cls:
        :return:
        """
        cmd_parser = argparse.ArgumentParser(add_help=False)
        cmd_parser.add_argument(
            '--async', default=False, required=False,
            action='store_true', dest="do_async",
            help="will use asyncio.")

        help_text = "command clear boot source pending values"
        return cmd_parser, "boot-options-clear", help_text

    def execute(self,
                do_async: Optional[bool] = False,
                data_type: Optional[str] = "json",
                **kwargs
                ) -> CommandResult:
        """Execute clear boot source pending values.

        :param do_async:
        :param data_type:
        :param kwargs:
        :return:
        """
        headers = {}
        if data_type == "json":
            headers.update(self.json_content_type)

        api_target = f"{self.idrac_manage_servers}/Oem/Dell/DellBootSources" \
                     "/Settings/Actions/DellManager.ClearPending"

        cmd_result, api_resp = self.base_post(
            api_target, payload={},
            do_async=do_async
        )

        if api_resp == IdracApiRespond.AcceptedTaskGenerated:
            task_id = cmd_result.data['task_id']
            task_state = self.fetch_task(task_id)
            cmd_result.data['task_state'] = task_state
            cmd_result.data['task_id'] = task_id

        return cmd_result


