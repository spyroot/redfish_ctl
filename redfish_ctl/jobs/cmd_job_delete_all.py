"""iDRAC deletes all jobs

Author Mus spyroot@gmail.com
"""
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




class JobRmDellServices(IDracManager,
                        scm_type=ApiRequestType.JobRmDellServices,
                        name='job_delete_all',
                        metaclass=Singleton):
    """A command query job_service_query.
    """

    def __init__(self, *args, **kwargs):
        super(JobRmDellServices, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command and all optional flags.
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser()
        help_text = "command deletes all existing job"
        return cmd_parser, "job-rm-all", help_text

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                do_force: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Executes deletes all jobs

        :param do_force:
        :param do_async: note async will subscribe to an event loop.
        :param do_expanded: will do expand query
        :param data_type: json or xml
        :param verbose: enables verbose output
        :param filename: if filename indicate call will save a bios setting to a file.
        :return: CommandResult and if filename provide will save to a file.
        """
        target_api = "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellJobService"
        cmd_result = self.base_query(target_api,
                                     filename=filename,
                                     do_async=do_async,
                                     do_expanded=do_expanded)

        actions = self.discover_redfish_actions(self, cmd_result.data)
        if do_force:
            payload = {'JobID': "JID_CLEARALL_FORCE"}
        else:
            payload = {"JobID": "JID_CLEARALL"}

        target_api = actions['DeleteJobQueue'].target
        cmd_result, api_resp = self.base_post(target_api, do_async=do_async,
                                              payload=payload,
                                              expected_status=200)
        if api_resp == IdracApiRespond.AcceptedTaskGenerated:
            task_id = cmd_result.data['task_id']
            task_state = self.fetch_task(task_id)
            cmd_result.data['task_state'] = task_state
            cmd_result.data['task_id'] = task_id

        return cmd_result
