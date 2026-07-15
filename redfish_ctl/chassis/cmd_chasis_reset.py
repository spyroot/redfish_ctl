"""Reset a chassis power state.

 redfish_ctl chassis-reset --reset_type ForceOff

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import FailedDiscoverAction, InvalidArgument, UnsupportedAction
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import REDFISH_JSON, ApiRequestType, RedfishApiRespond, Singleton
from ..redfish_manager import CommandResult


class ChassisReset(RedfishManagerBase,
                   scm_type=ApiRequestType.ChassisReset,
                   name='reboot',
                   metaclass=Singleton):
    """
    This  action resets the chassis but does not reset systems
    or other contained resources, although side effects can occur
    that affect those resources.
    """

    def __init__(self, *args, **kwargs):
        """Initialize the chassis-reset command."""
        super(ChassisReset, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser(is_file_save=False,
                                     is_expanded=False)
        cmd_parser.add_argument('--reset_type',
                                required=False, dest='reset_type',
                                default="On", type=str,
                                help="On, ForceOff. On will change power state..")

        help_text = "command change power state of a chassis"
        return cmd_parser, "chassis-reset", help_text

    def discover_reset(self, reset_type: str) -> str:
        """Method discover reset action, and return target api
        in order invoke reset.
        :param reset_type: On , or ForceOff
        :return: discovered action
        :raise InvalidArgument if reset type unknown
        """
        chassis_data = self.sync_invoke(
            ApiRequestType.ChassisQuery,
            "chassis_service_query",
            do_expanded=True
        )

        if 'Reset' not in chassis_data.discovered:
            raise UnsupportedAction(
                "Failed to discover the reset chassis action"
            )

        redfish_action = chassis_data.discovered[REDFISH_JSON.Reset]
        target_api = redfish_action.target

        # Validate the reset type only when the action advertised its allowable
        # values. Dell inlines ResetType@Redfish.AllowableValues; some boxes
        # (e.g. the GB300 capture) expose the Reset target with no inline
        # allowable values, leaving RedfishAction.args None -- defer validation
        # to the BMC in that case rather than raising.
        args = redfish_action.args
        if args and REDFISH_JSON.ResetType in args:
            args_options = args[REDFISH_JSON.ResetType]
            if reset_type not in args_options:
                raise InvalidArgument(
                    f"Unsupported reset type {reset_type} "
                    f"supported reset options {args_options}."
                )

        return target_api

    def execute(self,
                reset_type: Optional[str] = "On",
                do_async: Optional[bool] = False,
                data_type: Optional[str] = "json",
                do_watch: Optional[str] = True,
                **kwargs
                ) -> CommandResult:
        """
        Execute command and change chassis power state.
        :param do_async: async or not
        :param reset_type: "On, ForceOff"
        :param data_type: json or xml
        :param do_watch: wait and watch progress.
        :param kwargs: args passwd downstream
        :return: return cmd result
        :raise FailedDiscoverAction
        """
        headers = {}
        if data_type == "json":
            headers.update(self.json_content_type)

        target_api = self.discover_reset(reset_type)

        if target_api is None:
            raise FailedDiscoverAction(
                "Failed discover reset chassis actions."
            )

        payload = {
            REDFISH_JSON.ResetType: reset_type
        }
        cmd_result, api_resp = self.base_post(
            target_api, payload=payload, do_async=do_async
        )

        if api_resp == RedfishApiRespond.AcceptedTaskGenerated:
            task_id = cmd_result.data['task_id']
            cmd_result.data['task_id'] = task_id
            if do_watch:
                task_state = self.fetch_task(task_id)
                cmd_result.data['task_state'] = task_state

        return cmd_result
