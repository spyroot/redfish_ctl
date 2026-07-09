"""Flash firmware via Redfish UpdateService.SimpleUpdate (guarded).

    idrac_ctl firmware-update --image_uri http://host/fw.bin              # dry-run
    idrac_ctl firmware-update --image_uri http://host/fw.bin --confirm    # flash

Resolves ``#UpdateService.SimpleUpdate`` from the UpdateService's own Actions
block (no hardcoded id) and POSTs {ImageURI, TransferProtocol?} through the
shared ``invoke_action`` guard. SimpleUpdate is standard DMTF and works on any
host that advertises it (Dell, HPE iLO, ...).

DESTRUCTIVE: flashing disrupts/risks the target, so this defaults to a DRY-RUN
(prints the resolved target + payload, POSTs nothing) until ``--confirm``.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult
from ..redfish_shared import RedfishApi


class FirmwareUpdate(IDracManager,
                     scm_type=ApiRequestType.FirmwareUpdate,
                     name='firmware-update',
                     metaclass=Singleton):
    """Flash firmware via a discovered UpdateService.SimpleUpdate action."""

    def __init__(self, *args, **kwargs):
        super(FirmwareUpdate, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``firmware-update`` subcommand and its safety flags."""
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            '--image_uri', required=False, dest='image_uri', type=str, default=None,
            help="firmware image URI to flash (ImageURI in the payload)")
        cmd_parser.add_argument(
            '--transfer_protocol', required=False, dest='transfer_protocol',
            type=str, default=None, help="optional TransferProtocol (HTTP, HTTPS, ...)")
        cmd_parser.add_argument(
            '--confirm', action='store_true', dest='confirm',
            help="actually flash (without it this is a dry-run)")
        cmd_parser.add_argument(
            '--dry_run', action='store_true', dest='dry_run',
            help="force a dry-run preview even if --confirm is given")
        return cmd_parser, "firmware-update", "command flash firmware via SimpleUpdate (guarded)"

    def _update_service_uri(self, do_async):
        """Resolve the UpdateService URI from the service root, with a fallback."""
        try:
            root = self.base_query(RedfishApi.Version, do_async=do_async).data or {}
        except Exception:
            root = {}
        link = root.get("UpdateService")
        if isinstance(link, dict) and link.get("@odata.id"):
            return link["@odata.id"]
        return f"{RedfishApi.Version}/UpdateService"

    def execute(self,
                image_uri: Optional[str] = None,
                transfer_protocol: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Resolve SimpleUpdate and POST the image payload (guarded).

        Returns a dry-run preview unless ``--confirm``; the destructiveness guard
        lives in ``invoke_action``. ``image_uri`` is required to actually flash.
        """
        payload = {}
        if image_uri:
            payload["ImageURI"] = image_uri
        if transfer_protocol:
            payload["TransferProtocol"] = transfer_protocol
        return self.invoke_action(
            self._update_service_uri(do_async),
            "SimpleUpdate",
            payload=payload,
            full_action_type="#UpdateService.SimpleUpdate",
            do_async=do_async,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
        )
