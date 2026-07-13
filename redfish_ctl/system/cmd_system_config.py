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

from ..base_manager import CommandBase
from ..command_shared import ApiRequestType, RedfishCommandRespond, Singleton
from ..redfish_manager import CommandResult


class ExportSystemConfig(CommandBase,
                         scm_type=ApiRequestType.SystemConfigQuery,
                         name='sysconfig_query',
                         metaclass=Singleton):
    """
    Command exports system configuration..
    """

    def __init__(self, *args, **kwargs):
        super(ExportSystemConfig, self).__init__(*args, **kwargs)

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

        cmd_parser.add_argument('-f', '--filename', required=False, type=str,
                                default="",
                                help="filename if we need to save a respond to a file.")

        cmd_parser.add_argument('--export_use', action='store_true',
                                required=False, default=False,
                                help="Will create a task and will not wait.")

        help_text = "command exports system configuration"
        return cmd_parser, "system-export", help_text

    def execute(self,
                data_type: Optional[str] = "json",
                filename: Optional[str] = None,
                export_format: Optional[str] = "json",
                export_use: Optional[str] = "Default",
                include_in_export: Optional[str] = "Default",
                target: Optional[str] = "ALL",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs
                ) -> CommandResult:
        """Exports system configuration from idrac

        Default, Clone, Replace
        Default, IncludeReadOnly, IncludePasswordHashValues
        ShareParameters

        Share parameters and values
        - IP address of the network share
        - Name of network share
        - File name for the SCP
        - CIFS, NFS, HTTP, HTTPS
        - Username to log on to the share — for CIFS share only.
        - Password to log on to the share — for CIFS share only.
        - Workgroup name to log on to the share
        - Can be the component name or an FQDN. The default value is ALL.

        :param do_async:
        :param data_type:
        :param verbose:
        :param filename: if filename indicate call will save a bios setting to a file.
        :param include_in_export:
        :param target:
        :param export_format:
        :param export_use:
        :return:
        """

        if verbose:
            self.logger.debug(f"cmd args data_type: {data_type} "
                  f"do_async:{do_async} export_format:{export_format}")
            self.logger.debug(f"the rest of args: {kwargs}")

        headers = {}
        headers.update(self.content_type)
        payload = {
            "ExportFormat": export_format.upper(),
            "ShareParameters": {
                "Target": target,
                "FileName": "",
            },
            "IncludeInExport": include_in_export
        }

        if "Clone" in export_use or "Replace" in export_use:
            payload["ExportUse"] = export_use

        r = f"{self._default_method}{self.idrac_ip}/redfish/v1/Managers/iDRAC.Embedded.1/" \
            f"Actions/Oem/EID_674_Manager.ExportSystemConfiguration"

        # json_pd = json.dumps(payload)

        cmd_result, api_resp = self.base_post(r, payload=payload,
                                              do_async=do_async,
                                              expected_status=202)

        if api_resp == RedfishCommandRespond.AcceptedTaskGenerated:
            task_id = cmd_result.data['task_id']
            task_state = self.fetch_task(task_id)
            cmd_result.data['task_state'] = task_state
            cmd_result.data['task_id'] = task_id


        #resp_hdr = response.headers
        # if 'Location' not in resp_hdr:
        #     raise UnexpectedResponse("rest api failed.")
        #
        # location = response.headers['Location']
        # job_id = location.split("/")[-1]
        # if not do_async:
        #     data = self.fetch_task(job_id)
        #     # we save only if we need to.
        #     if verbose:
        #         print(f"Saving to a file {filename}")
        #     save_if_needed(filename, data)
        # else:
        #     data = {"job_id": job_id}

        return cmd_result
