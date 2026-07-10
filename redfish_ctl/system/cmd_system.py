"""iDRAC system command

Command provides the option to retrieve system view from iDRAC and serialize
back as caller as JSON, YAML, and XML. In addition, it automatically
registers to the command line ctl tool. Similarly to the rest of commands
caller can save to a file and consume asynchronously or synchronously.

Author Mus spyroot@gmail.com
"""
import argparse
from abc import abstractmethod
from typing import Optional
from ..idrac_manager import CommandResult, IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..cmd_utils import save_if_needed


class SystemQuery(IDracManager,
                  scm_type=ApiRequestType.SystemQuery,
                  name='system_query',
                  metaclass=Singleton):
    """This main compute system query rest call.

    By default, will output system view without going deeper.
    In case caller provide do_deep will execute each respected rest_api
    and aggregate result.
    """

    def __init__(self, *args, **kwargs):
        super(SystemQuery, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register commands args
        :param cls:
        :return:
        """
        cmd_parser = argparse.ArgumentParser(add_help=False)
        cmd_parser.add_argument('--sub_option', action='store_true',
                                required=False, dest='module',
                                help="fetch main compute system view.")
        # --deep sub-command.
        cmd_parser.add_argument('--deep', action='store_true', required=False, dest="do_deep",
                                default=False, help="deep walk. will make a separate "
                                                    "REST call for each rest api.")

        cmd_parser.add_argument('-f', '--filename', required=False, type=str,
                                default="",
                                help="filename if we need to save a respond to a file.")

        cmd_parser.add_argument('-s', '--save_all', required=False, type=str, dest="do_save",
                                default=False, help="for deep walk by default we don't "
                                                    "save result to a file. save_all "
                                                    "will save to a separate file.")

        cmd_parser.add_argument('--save_dir', required=False, type=str, dest="save_dir",
                                default=False, help="will save json files in separate directory.")

        cmd_parser.add_argument('--async', action='store_true', required=False, dest="do_async",
                                default=False, help="Will create a task and will not wait.")

        help_text = "command fetch the system view."
        return cmd_parser, "system", help_text

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                do_deep: Optional[bool] = False,
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                save_dir: Optional[str] = None, **kwargs) -> CommandResult:
        """Execute main system query. From here we see what system support.
        Power state / and overall view.

        Method return actual json and dict where each key is action
        and respected reset end point.

        Bios: /redfish/v1/Systems/System.Embedded.1/Bios
        Memory /redfish/v1/Systems/System.Embedded.1/Memory
        Processors /redfish/v1/Systems/System.Embedded.1/Processors
        SecureBoot /redfish/v1/Systems/System.Embedded.1/SecureBoot
        SimpleStorage /redfish/v1/Systems/System.Embedded.1/SimpleStorage
        Storage /redfish/v1/Systems/System.Embedded.1/Storage
        VirtualMedia /redfish/v1/Systems/System.Embedded.1/VirtualMedia
        EthernetInterfaces: /redfish/v1/Systems/System.Embedded.1/EthernetInterfaces
        NetworkInterfaces /redfish/v1/Systems/System.Embedded.1/NetworkInterfaces

        :param save_dir:
        :param do_async:
        :param verbose:
        :param do_deep: if caller indicate deep, method will perform deep call
                     for each end point.
        :param filename: if filename indicate call will save a bios setting to a file.
        :param data_type:  what data type we use to serialize data.
        :return: json or xml and dict that host type of action and rest api end point.
        """
        if verbose:
            print(f"filename {filename} data type: {data_type} "
                  f"do_deep: {do_deep}, do_async: {do_async}, "
                  f"save_dir: {save_dir}")

        headers = {}
        if data_type == "json":
            headers.update(self.json_content_type)

        r = f"{self._default_method}{self.idrac_ip}{self.idrac_manage_servers}"
        response = self.api_get_call(r, headers)
        self.default_error_handler(response)
        data = response.json()
        save_if_needed(filename, data, save_dir=save_dir)

        rest_endpoints = {}
        extra_data_dict = {}

        for k in data.keys():
            if isinstance(data[k], dict) and "@odata.id" in data[k]:
                sub_rest = data[k]["@odata.id"]
                rest_endpoints[k] = sub_rest
                # deep walk
                if do_deep:
                    r = f"{self._default_method}{self.idrac_ip}{sub_rest}"
                    response = self.api_get_call(r, headers)
                    self.default_error_handler(response)
                    if verbose:
                        print(f"sending request {r} status code {response.status_code}")
                    extra_data_dict[k] = response.json()

        return CommandResult(data, rest_endpoints, extra_data_dict, None)
