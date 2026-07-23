"""Mount an ISO as a virtual CD-ROM over the standard Redfish VirtualMedia API.

This is the DMTF ``VirtualMedia.InsertMedia`` action (``@odata.type``
``#VirtualMedia.v1_x``) — the same operation the Redfish endpoint advertises in a
VirtualMedia member's ``Actions`` block. The command targets any Redfish BMC that
exposes ``#VirtualMedia.InsertMedia`` (Dell iDRAC, OpenBMC/Supermicro GB300, HPE
iLO, generic Redfish); it does not hardcode a Manager id — the InsertMedia target
is discovered from the resource, so the ``Managers/BMC_0`` path on a GB300 works
the same as the Dell ``Systems/System.Embedded.1`` path.

With no ``--device_id`` the command auto-selects the first VirtualMedia device that
advertises a CD/DVD ``MediaType`` and an ``InsertMedia`` action, so the operator
does not need to know the vendor-specific device id (Dell ``1`` vs GB300 ``USB1``).

Older Supermicro X10 boards that do not implement ``VirtualMedia.InsertMedia`` (an
empty ``/VirtualMedia`` collection) use the OEM CfgCD path instead — see the
``vm-mount`` command (``SmcVirtualMediaMount``); this command is the DMTF form.

    redfish_ctl mount_cdrom --uri_path http://10.0.0.5/ubuntu-24.04.iso
    redfish_ctl mount_cdrom --uri_path http://10.0.0.5/rhel.iso --device_id USB1
    redfish_ctl mount_cdrom --uri_path https://share/win.iso --eject

Author Mus spyroot@gmail.com
"""
import argparse
from abc import abstractmethod
from typing import List, Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, RedfishApiRespond, Singleton
from ..redfish_manager import CommandResult


class MountCdrom(IDracManager,
                 scm_type=ApiRequestType.VirtualMediaInsert,
                 name='mount_cdrom',
                 metaclass=Singleton):
    """Mount an ISO image as a virtual CD-ROM via ``VirtualMedia.InsertMedia``.

    A thin, CD-ROM-oriented front end over the DMTF InsertMedia action: it
    discovers a CD-capable, insertable VirtualMedia device, builds the standard
    InsertMedia request body, and lets the manager chokepoint absorb an async
    task (202) if the BMC returns one. A domain failure (missing image, no
    CD-capable device, media already inserted) is returned as
    ``CommandResult.error``; it is never raised.
    """

    def __init__(self, *args, **kwargs):
        """Initialize the ``mount_cdrom`` command."""
        super(MountCdrom, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``mount_cdrom`` sub-command and its arguments.

        :param cls: the command class registering itself.
        :return: a tuple of (ArgumentParser, CLI verb, help text).
        """
        cmd_arg = argparse.ArgumentParser(add_help=False)

        cmd_arg.add_argument('--uri_path', required=True, type=str,
                             default=None,
                             help="URI to the ISO image, e.g. "
                                  "http://1.1.1.1/installer.iso")

        cmd_arg.add_argument('--device_id', required=False, type=str,
                             default="",
                             help="explicit VirtualMedia device id "
                                  "(e.g. 1 or USB1); empty auto-selects a "
                                  "CD-capable device that supports InsertMedia")

        cmd_arg.add_argument('--remote_username', required=False, type=str,
                             default=None,
                             help="username for the remote media share, "
                                  "if the transfer requires authentication")

        cmd_arg.add_argument('--remote_password', required=False, type=str,
                             default=None,
                             help="password for the remote media share, "
                                  "if the transfer requires authentication")

        cmd_arg.add_argument('--eject',
                             action='store_true',
                             required=False, dest="do_eject",
                             help="eject any currently inserted media on the "
                                  "target device before mounting")

        cmd_arg.add_argument('--confirm',
                             action='store_true',
                             required=False, dest="confirm", default=False,
                             help="perform the mount; without it the command is "
                                  "a dry run that reports the target and payload")

        help_text = "mount an ISO as a virtual CD-ROM (Redfish InsertMedia)"
        return cmd_arg, "mount_cdrom", help_text

    @staticmethod
    def _is_cdrom_capable(member: dict) -> bool:
        """Report whether a VirtualMedia member can hold optical (CD/DVD) media.

        :param member: a VirtualMedia device dict from the collection.
        :return: True when ``MediaTypes`` advertises CD or DVD, else False.
        """
        media_types = member.get('MediaTypes') or []
        return any(str(m).upper() in ('CD', 'DVD') for m in media_types)

    @staticmethod
    def _has_insert_action(member: dict) -> bool:
        """Report whether a VirtualMedia member advertises an InsertMedia action.

        :param member: a VirtualMedia device dict from the collection.
        :return: True when the member's ``Actions`` block names InsertMedia.
        """
        actions = member.get('Actions') or {}
        return any('InsertMedia' in str(key) for key in actions)

    def _select_device(self,
                       members: List[dict],
                       device_id: Optional[str]) -> Optional[dict]:
        """Select the VirtualMedia device to mount onto.

        With an explicit ``device_id`` the matching member is returned. Otherwise
        the first CD-capable member that advertises an InsertMedia action is
        chosen, so the caller need not know the vendor-specific device id.

        :param members: VirtualMedia device dicts from the collection.
        :param device_id: an explicit device id, or empty/None to auto-select.
        :return: the chosen member dict, or None when nothing matches.
        """
        if device_id is not None and len(str(device_id).strip()) > 0:
            wanted = str(device_id).strip()
            for member in members:
                if member.get('Id') == wanted:
                    return member
            return None

        for member in members:
            if self._is_cdrom_capable(member) and self._has_insert_action(member):
                return member
        return None

    def execute(self,
                uri_path: Optional[str] = None,
                device_id: Optional[str] = "",
                remote_username: Optional[str] = None,
                remote_password: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_eject: Optional[bool] = False,
                confirm: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Mount an ISO image as a virtual CD-ROM via the InsertMedia action.

        The VirtualMedia collection is resolved vendor-neutrally, a CD-capable
        insertable device is selected, the InsertMedia target is discovered from
        that device's ``Actions`` block, and the DMTF InsertMedia body is POSTed.
        When the BMC answers with a task (202), the manager chokepoint surfaces
        the job id and this command polls it to a terminal state.

        :param uri_path: URI to the ISO image to mount (required).
        :param device_id: explicit VirtualMedia device id; empty auto-selects a
            CD-capable device that supports InsertMedia.
        :param remote_username: username for the remote media share, if required.
        :param remote_password: password for the remote media share, if required.
        :param data_type: content type for the request; json or xml.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: return the job id without blocking on task completion.
        :param do_eject: eject currently inserted media before mounting.
        :param confirm: when False (default) the command is a dry run that reports
            the target device and InsertMedia payload without mounting; pass
            ``--confirm`` to perform the mount.
        :return: a CommandResult; a domain failure rides in ``error`` as a string.
        :raise: AuthenticationFailed, PostRequestFailed on transport failures.
        """
        if uri_path is None or len(str(uri_path).strip()) == 0:
            status = ("an ISO image URI is required; pass "
                      "--uri_path <http(s)://host/image.iso>")
            return CommandResult({"Status": status}, None, None, status)

        headers = {}
        if data_type == "json":
            headers.update(self.json_content_type)

        virtual_media = self.sync_invoke(
            ApiRequestType.VirtualMediaGet,
            "virtual_disk_query"
        )
        if virtual_media.error is not None:
            return virtual_media

        data = virtual_media.data if isinstance(virtual_media.data, dict) else {}
        members = data.get('Members')
        if not isinstance(members, list) or len(members) == 0:
            status = "no VirtualMedia devices are exposed by this Redfish endpoint"
            return CommandResult({"Status": status}, None, None, status)

        target = self._select_device(members, device_id)
        if target is None:
            available = [m.get('Id') for m in members if isinstance(m, dict)]
            if device_id is not None and len(str(device_id).strip()) > 0:
                status = (f"device id {device_id} not found; "
                          f"available devices: {available}")
            else:
                status = ("no CD-capable VirtualMedia device with an "
                          "InsertMedia action was found; "
                          f"available devices: {available}")
            return CommandResult({"Status": status}, None, None, status)

        actions = self.discover_redfish_actions(self, target)
        insert_action = actions.get('InsertMedia')
        if insert_action is None:
            status = (f"device {target.get('Id')} does not expose a "
                      f"VirtualMedia.InsertMedia action")
            return CommandResult({"Status": status}, None, None, status)

        if not confirm:
            return CommandResult(
                {"dry_run": True,
                 "device": target.get('Id'),
                 "insert_target": insert_action.target,
                 "image": uri_path,
                 "currently_inserted":
                     target.get('Image') if target.get('Inserted') else None,
                 "hint": "re-run with --confirm to mount"},
                None, None, None)

        if target.get('Inserted'):
            if not do_eject:
                status = (f"device {target.get('Id')} already has "
                          f"{target.get('Image')} inserted; "
                          f"eject first or pass --eject")
                return CommandResult({"Status": status}, None, None, status)
            eject_result = self.sync_invoke(
                ApiRequestType.VirtualMediaEject,
                "virtual_disk_eject",
                device_id=target.get('Id')
            )
            if eject_result.error is not None:
                return eject_result

        # DMTF VirtualMedia.InsertMedia request body. Image is the only required
        # parameter; a CD-ROM is read-only so WriteProtected is always True.
        payload = {
            'Image': uri_path,
            'Inserted': True,
            'WriteProtected': True,
            'UserName': remote_username,
            'Password': remote_password,
        }
        for key, value in dict(payload).items():
            if value is None:
                del payload[key]

        cmd_result, api_resp = self.base_post(
            insert_action.target, payload=payload,
            do_async=do_async, expected_status=202
        )

        if api_resp == RedfishApiRespond.AcceptedTaskGenerated:
            task_id = cmd_result.data['task_id']
            self.logger.info(f"Fetching task {task_id} state.")
            task_state = self.fetch_task(task_id)
            cmd_result.data['task_state'] = task_state
            cmd_result.data['task_id'] = task_id

        return cmd_result
