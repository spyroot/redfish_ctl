"""Supermicro OEM virtual media (CfgCD / IsoConfig) — mount / unmount / status.

Supermicro X10/X11 BMCs do NOT implement standard Redfish
``VirtualMedia.InsertMedia`` (their ``/VirtualMedia`` collection is empty). They
mount an ISO from a CIFS/NFS share via the OEM resource under the Manager:

    /redfish/v1/Managers/<id>/VM1/CfgCD                          Host/Path/Username/Password
    /redfish/v1/Managers/<id>/VM1/CfgCD/Actions/IsoConfig.Mount
    /redfish/v1/Managers/<id>/VM1/CfgCD/Actions/IsoConfig.UnMount
    /redfish/v1/Managers/<id>/VM1/CD1
        status: Inserted / ImageName / Image

The VM1 resource advertises the CfgCD resource through
Oem.Supermicro.VirtualMediaConfig. Status output keeps the legacy Members/CD1
keys and adds the resolved CfgCD path, sanitized CfgCD fields, and per-path read
diagnostics so unavailable X10 resources are explicit instead of bare nulls.

Requires the SFT-DCMS-SINGLE license active on the BMC. Old X10 BMCs speak only
SMB1/NT1, so the CIFS server must offer NT1 (see home_automation/serve_media.sh).

Examples:
    redfish_ctl vm-mount --host 192.168.254.192 --path /dl/ubuntu.iso \
        --user iso --password isopass123
    redfish_ctl vm-mount --status
    redfish_ctl vm-mount --unmount

Author Mus spyroot@gmail.com
"""
import argparse
from abc import abstractmethod
from typing import Any, Optional

from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton


class SmcVirtualMediaMount(RedfishManagerBase,
                           scm_type=ApiRequestType.SmcVirtualMediaMount,
                           name='vm-mount',
                           metaclass=Singleton):
    """Supermicro OEM virtual media: mount / unmount / status an ISO via CfgCD."""

    def __init__(self, *args, **kwargs):
        """Initialize the vm-mount command."""
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
                                  "global --idrac_password/user collision)")
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
        so these helpers deal in paths; only api_get_call needs the full URL.

        :param manager_id: BMC manager id.
        :return: OEM VM1 resource path for the given manager.
        """
        return f"/redfish/v1/Managers/{manager_id}/VM1"

    def _read_json(self, path: str) -> dict[str, Any]:
        """Best-effort GET returning data plus path/status diagnostics.

        :param path: resource path to fetch; the host/URL prefix is added here.
        :return: read metadata with decoded JSON data when the read succeeded.
        """
        result: dict[str, Any] = {"path": path, "status": None, "ok": False}
        try:
            resp = self.api_get_call(f"{self._default_method}{self.idrac_ip}{path}", {})
            if resp is None:
                result["error"] = "no response"
                return result
            result["status"] = resp.status_code
            if resp.status_code != 200:
                result["error"] = f"HTTP {resp.status_code}"
                return result
            try:
                result["data"] = resp.json()
            except Exception as exc:
                result["error"] = f"invalid JSON: {exc}"
                return result
            result["ok"] = True
            return result
        except Exception as exc:
            result["error"] = str(exc)
            return result

    @staticmethod
    def _data_or_none(read: dict[str, Any]) -> Optional[dict]:
        """Return decoded data from a successful read.

        :param read: metadata returned by ``_read_json``.
        :return: decoded JSON dict, or None.
        """
        data = read.get("data")
        return data if isinstance(data, dict) else None

    @staticmethod
    def _read_summary(read: dict[str, Any]) -> dict[str, Any]:
        """Return public-safe status metadata for one attempted read.

        :param read: metadata returned by ``_read_json``.
        :return: path/status/ok plus error when present.
        """
        summary = {
            "path": read.get("path"),
            "status": read.get("status"),
            "ok": read.get("ok", False),
        }
        if read.get("error"):
            summary["error"] = read["error"]
        return summary

    @staticmethod
    def _cfgcd_path(vm1: dict[str, Any], fallback: str) -> str:
        """Resolve the advertised Supermicro CfgCD config link.

        :param vm1: decoded VM1 collection resource.
        :param fallback: default CfgCD path for older X10 resources.
        :return: CfgCD resource path.
        """
        oem = vm1.get("Oem") if isinstance(vm1, dict) else None
        supermicro = oem.get("Supermicro") if isinstance(oem, dict) else None
        config = (
            supermicro.get("VirtualMediaConfig")
            if isinstance(supermicro, dict)
            else None
        )
        config_path = config.get("@odata.id") if isinstance(config, dict) else None
        return config_path if isinstance(config_path, str) and config_path else fallback

    @staticmethod
    def _public_cfgcd_fields(cfgcd: dict[str, Any]) -> dict[str, Any]:
        """Return CfgCD fields useful for status without echoing passwords.

        :param cfgcd: decoded CfgCD resource.
        :return: status/config fields safe to show in command output.
        """
        keys = (
            "Host",
            "Path",
            "Username",
            "ShareType",
            "Inserted",
            "ImageName",
            "Image",
            "MediaTypes",
        )
        return {key: cfgcd.get(key) for key in keys if key in cfgcd}

    def _status(self, manager_id: str) -> dict:
        """Read VM1, CfgCD, and CD1 to report mount state.

        :param manager_id: BMC manager id.
        :return: dict with legacy Members/CD1 keys plus the resolved CfgCD
            path, sanitized CfgCD fields, and per-path read diagnostics.
        """
        vm1_path = self._vm1(manager_id)
        vm1_read = self._read_json(vm1_path)
        vm1 = self._data_or_none(vm1_read) or {}
        cfgcd_path = self._cfgcd_path(vm1, vm1_path + "/CfgCD")
        cfgcd_read = self._read_json(cfgcd_path)
        cfgcd = self._data_or_none(cfgcd_read)
        cd1_path = vm1_path + "/CD1"
        cd1_read = self._read_json(cd1_path)
        cd1 = self._data_or_none(cd1_read)
        return {
            "Members": vm1.get("Members"),
            "VirtualMediaConfig": cfgcd_path,
            "CfgCD": None if cfgcd is None else self._public_cfgcd_fields(cfgcd),
            "CD1": None if cd1 is None else {
                k: cd1.get(k) for k in ("Inserted", "ImageName", "Image", "MediaTypes")
            },
            "reads": {
                "VM1": self._read_summary(vm1_read),
                "CfgCD": self._read_summary(cfgcd_read),
                "CD1": self._read_summary(cd1_read),
            },
        }

    def _resolve_cfgcd_path(self, manager_id: str) -> str:
        """Resolve the Supermicro CfgCD target used for mount and unmount.

        :param manager_id: BMC manager id.
        :return: advertised CfgCD path, or the conventional X10 path.
        """
        vm1_path = self._vm1(manager_id)
        vm1_read = self._read_json(vm1_path)
        vm1 = self._data_or_none(vm1_read) or {}
        return self._cfgcd_path(vm1, vm1_path + "/CfgCD")

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
        :param share_user: share username (optional).
        :param share_pass: share password (optional).
        :param do_unmount: unmount instead of mount.
        :param do_status: report status only.
        :param manager_id: BMC manager id (Supermicro default "1").
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: accepted for CLI compatibility; not used by this command.
        :return: CommandResult.
        """
        if do_status:
            return CommandResult({"status": self._status(manager_id)}, None, None, None)

        if do_unmount:
            cfgcd = self._resolve_cfgcd_path(manager_id)
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
        cfgcd = self._resolve_cfgcd_path(manager_id)
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
