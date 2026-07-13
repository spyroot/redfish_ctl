"""Supermicro OEM virtual media (CfgCD / IsoConfig) — mount / unmount / status.

Supermicro X10/X11 BMCs do NOT implement standard Redfish
``VirtualMedia.InsertMedia`` (their ``/VirtualMedia`` collection is empty). They
mount an ISO from a CIFS/NFS share via the OEM resource under the Manager:

    /redfish/v1/Managers/<id>/VM1/CfgCD                          Host/Path/Username/Password
    /redfish/v1/Managers/<id>/VM1/CfgCD/Actions/IsoConfig.Mount
    /redfish/v1/Managers/<id>/VM1/CfgCD/Actions/IsoConfig.UnMount
    /redfish/v1/Managers/<id>/VM1/CD1                            status: Inserted / ImageName / Image

Requires the SFT-DCMS-SINGLE license active on the BMC. Old X10 BMCs speak only
SMB1/NT1, so the CIFS server must offer NT1 (see home_automation/serve_media.sh).

Examples:
    redfish_ctl vm-mount --host 192.168.254.192 --path /dl/ubuntu.iso --user iso --password isopass123
    redfish_ctl vm-mount --status
    redfish_ctl vm-mount --unmount

Author Mus spyroot@gmail.com
"""
import argparse
from abc import abstractmethod
from typing import Optional

from ..base_manager import CommandBase
from ..command_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult


class SmcVirtualMediaMount(CommandBase,
                           scm_type=ApiRequestType.SmcVirtualMediaMount,
                           name='vm-mount',
                           metaclass=Singleton):
    """Supermicro OEM virtual media: mount / unmount / status an ISO via CfgCD."""

    def __init__(self, *args, **kwargs):
        super(SmcVirtualMediaMount, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Registers command args.

        :param cls: registration hook
        :return: (parser, cli-name, help)
        """
        cmd_arg = argparse.ArgumentParser(add_help=False)
        cmd_arg.add_argument('--host', required=False, type=str, default=None,
                             help="CIFS/NFS share host serving the ISO, e.g. 192.168.254.192")
        cmd_arg.add_argument('--path', required=False, type=str, default=None,
                             help="path to the ISO on the share, e.g. /dl/ubuntu.iso")
        cmd_arg.add_argument('--share_user', required=False, type=str, default="",
                             help="CIFS share username (optional; renamed to avoid the "
                                  "global --redfish_password/user collision)")
        cmd_arg.add_argument('--share_pass', required=False, type=str, default="",
                             help="CIFS share password (optional)")
        cmd_arg.add_argument('--unmount', action='store_true', dest="do_unmount",
                             help="unmount the currently mounted ISO")
        cmd_arg.add_argument('--status', action='store_true', dest="do_status",
                             help="report mount status only (no change)")
        cmd_arg.add_argument('--manager_id', required=False, type=str, default="1",
                             help="BMC manager id (Supermicro default 1)")
        help_text = "command mount/unmount an ISO via Supermicro OEM virtual media (CfgCD)"
        return cmd_arg, "vm-mount", help_text

    def _vm1(self, manager_id: str) -> str:
        """Base OEM virtual-media PATH. base_post/base_patch prepend the host,
        so these helpers deal in paths; only api_get_call needs the full URL."""
        return f"/redfish/v1/Managers/{manager_id}/VM1"

    def _get(self, path: str) -> Optional[dict]:
        """Best-effort GET (path -> full URL) returning JSON or None (never raises)."""
        try:
            resp = self.api_get_call(f"{self._default_method}{self.idrac_ip}{path}", {})
            if resp is not None and resp.status_code == 200:
                return resp.json()
        except Exception:
            return None
        return None

    def _status(self, manager_id: str) -> dict:
        """Read the VM1 collection + CD1 device to report mount state."""
        vm1 = self._get(self._vm1(manager_id)) or {}
        cd1 = self._get(self._vm1(manager_id) + "/CD1")
        return {
            "Members": vm1.get("Members"),
            "CD1": None if cd1 is None else {
                k: cd1.get(k) for k in ("Inserted", "ImageName", "Image", "MediaTypes")
            },
        }

    def execute(self,
                host: Optional[str] = None,
                path: Optional[str] = None,
                share_user: Optional[str] = "",
                share_pass: Optional[str] = "",
                do_unmount: Optional[bool] = False,
                do_status: Optional[bool] = False,
                manager_id: Optional[str] = "1",
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Mount / unmount / report status of a Supermicro OEM virtual CD.

        :param host: CIFS/NFS share host.
        :param path: path to the ISO on the share (e.g. /dl/ubuntu.iso).
        :param user: share username (optional).
        :param password: share password (optional).
        :param do_unmount: unmount instead of mount.
        :param do_status: report status only.
        :param manager_id: BMC manager id (Supermicro default "1").
        :return: CommandResult.
        """
        cfgcd = self._vm1(manager_id) + "/CfgCD"

        if do_status:
            return CommandResult({"status": self._status(manager_id)}, None, None, None)

        if do_unmount:
            r, _ = self.base_post(cfgcd + "/Actions/IsoConfig.UnMount",
                                  payload={}, expected_status=200)
            if r.error is not None:
                return r
            return CommandResult({"unmounted": True, "status": self._status(manager_id)},
                                 None, None, None)

        if not host or not path:
            return CommandResult(
                {"error": "--host and --path are required to mount "
                          "(or use --status / --unmount)"}, None, None, None)

        # 1) configure the share (CfgCD), 2) mount it (IsoConfig.Mount)
        cfg, _ = self.base_patch(
            cfgcd,
            payload={"Host": host, "Path": path,
                     "Username": share_user or "", "Password": share_pass or ""},
            expected_status=200)
        if cfg.error is not None:
            return cfg
        r, _ = self.base_post(cfgcd + "/Actions/IsoConfig.Mount",
                              payload={}, expected_status=200)
        if r.error is not None:
            return r
        return CommandResult({"mounted": True, "host": host, "path": path,
                              "status": self._status(manager_id)}, None, None, None)
