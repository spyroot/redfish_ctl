"""Reset the manager.

Reset/reboot the Redfish manager.

    redfish_ctl manager-reboot

Author Mus spyroot@gmail.com
"""
import argparse
from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, RedfishApiRespond, Singleton
from ..redfish_manager import CommandResult


class ManagerReset(IDracManager,
                   scm_type=ApiRequestType.ManagerReset,
                   name='manager_reset',
                   metaclass=Singleton):
    """Reset the manager command, targets the manager service,
    caller can save to a file or output to a file or pass downstream.
    """

    def __init__(self, *args, **kwargs):
        """Initialize the manager-reboot command."""
        super(ManagerReset, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Registers command args
        :param cls:
        :return:
        """
        cmd_arg = argparse.ArgumentParser(add_help=False)

        cmd_arg.add_argument(
            '--async', action='store_true',
            required=False, dest="do_async",
            default=False,
            help="Will create a task and will not wait.")

        cmd_arg.add_argument(
            '--graceful', action='store_true',
            required=False, dest="do_graceful",
            default=True, help="do graceful reset.")

        cmd_arg.add_argument(
            '--wait', action='store_true',
            required=False, dest="do_wait", default=False,
            help="after the reset, wait for the BMC to go DOWN then come back "
                 "reachable (ServiceRoot), instead of polling a reset task the "
                 "down BMC cannot report.")

        cmd_arg.add_argument(
            '--wait-timeout', required=False, type=float,
            dest="wait_timeout", default=300.0,
            help="with --wait: max seconds to wait for the BMC (default 300).")

        help_text = "command reboot idrac manager"
        return cmd_arg, "manager-reboot", help_text

    def execute(self,
                data_type: Optional[str] = "json",
                do_deep: Optional[bool] = False,
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_graceful: Optional[bool] = True,
                do_wait: Optional[bool] = False,
                wait_timeout: Optional[float] = 300.0,
                **kwargs) -> CommandResult:
        """Reset the manager services.

        :param data_type: json or xml; ``json`` adds the JSON content-type header.
        :param do_deep: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: create a task and return without waiting for it.
        :param do_graceful: request a ``GracefulRestart`` (default); otherwise send an empty payload.
        :param do_wait: after the reset, poll ServiceRoot until the BMC goes down then becomes reachable again.
        :param wait_timeout: with ``do_wait``, the maximum seconds to wait for the BMC to cycle.
        :return: CommandResult from the reset POST, with task_state/task_id added when a
            task is generated and a ``wait`` result added when ``do_wait`` is set.
        :raise: AuthenticationFailed, UnexpectedResponse
        """
        headers = {}
        if data_type == "json":
            headers.update(self.json_content_type)

        t = f"{self.idrac_members}/Actions/Manager.Reset"
        if do_graceful:
            pd = {
                "ResetType": "GracefulRestart"
            }
        else:
            pd = {}

        cmd_result, api_resp = self.base_post(
            t, payload=pd, do_async=do_async,
            expected_status=202
        )

        if api_resp == RedfishApiRespond.AcceptedTaskGenerated:
            task_id = cmd_result.data['task_id']
            task_state = self.fetch_task(task_id)
            cmd_result.data['task_state'] = task_state
            cmd_result.data['task_id'] = task_id

        # Optionally wait for the BMC to actually cycle. A Manager reset takes the
        # BMC itself offline, so we poll ServiceRoot reachability (down then up)
        # rather than a reset task the down BMC cannot answer.
        if do_wait:
            from ..cmd_wait import wait_reachable
            scheme = "http" if self._is_http else "https"
            url = f"{scheme}://{self.redfish_ip}:{self._port}/redfish/v1/"
            auth = (self._username, self._password) if self._username else None
            wr = wait_reachable(url, auth, self._is_verify_cert,
                                wait_timeout, 5.0, reboot_cycle=True)
            if isinstance(cmd_result.data, dict):
                cmd_result.data["wait"] = wr
            else:
                cmd_result = CommandResult({"reset": cmd_result.data, "wait": wr},
                                           None, None, cmd_result.error)

        return cmd_result
