"""Preview or initialize Dell vFlash media.

    redfish_ctl dell-persistent-initialize-media
    redfish_ctl dell-persistent-initialize-media --confirm \
        --i-understand-irreversible

The command discovers ``#DellPersistentStorageService.InitializeMedia`` from the
Manager OEM Dell persistent-storage service. Initializing vFlash media can erase
stored partition data, so the command previews by default and only POSTs when
both confirmation flags are supplied.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..actions.action_policy import classify
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult
from ..redfish_shared import RedfishApi

_INITIALIZE_MEDIA_ACTION = "#DellPersistentStorageService.InitializeMedia"
_SERVICE_NAME = "DellPersistentStorageService"
_DEFAULT_SERVICE_URI = (
    f"{RedfishApi.Version}/Managers/iDRAC.Embedded.1/Oem/Dell/{_SERVICE_NAME}"
)


class DellPersistentInitializeMedia(
    IDracManager,
    scm_type=ApiRequestType.DellPersistentInitializeMedia,
    name="dell-persistent-initialize-media",
    metaclass=Singleton,
):
    """Discover and invoke DellPersistentStorageService.InitializeMedia."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-persistent-initialize-media command."""
        super(DellPersistentInitializeMedia, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded ``dell-persistent-initialize-media`` subcommand.

        :param cls: command class supplying the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--resource-uri",
            dest="resource_uri",
            default=None,
            help="specific DellPersistentStorageService URI when discovery needs override",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            dest="confirm",
            default=False,
            help="authorize the InitializeMedia POST",
        )
        cmd_parser.add_argument(
            "--i-understand-irreversible",
            action="store_true",
            dest="confirm_irreversible",
            default=False,
            help="required with --confirm because media initialization can erase data",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="resolve the target without POSTing; overrides confirmation",
        )
        return (
            cmd_parser,
            "dell-persistent-initialize-media",
            "preview or initialize Dell vFlash media",
        )

    @staticmethod
    def _link(data, key):
        """Return an ``@odata.id`` link value from a Redfish object.

        :param data: Redfish resource body.
        :param key: link property name.
        :return: linked URI, or None when absent or malformed.
        """
        link = data.get(key) if isinstance(data, dict) else None
        return link.get("@odata.id") if isinstance(link, dict) else None

    @classmethod
    def _dell_oem_link(cls, manager):
        """Return the persistent-storage service link from Manager OEM data.

        :param manager: Manager resource body.
        :return: linked DellPersistentStorageService URI, or None.
        """
        links = manager.get("Links") if isinstance(manager, dict) else None
        oem_links = links.get("Oem") if isinstance(links, dict) else None
        dell_links = oem_links.get("Dell") if isinstance(oem_links, dict) else None
        linked = cls._link(dell_links or {}, _SERVICE_NAME)
        if linked:
            return linked
        oem = manager.get("Oem") if isinstance(manager, dict) else None
        dell = oem.get("Dell") if isinstance(oem, dict) else None
        return cls._link(dell or {}, _SERVICE_NAME)

    def _get(self, uri, do_async):
        """GET a Redfish resource body, returning an empty object on failure.

        :param uri: Redfish resource URI.
        :param do_async: issue the query on the async path when True.
        :return: parsed resource body, or an empty dict when absent.
        """
        try:
            data = self.base_query(uri.rstrip("/"), do_async=do_async).data or {}
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _service_uris(self, do_async):
        """Return candidate DellPersistentStorageService URIs.

        :param do_async: issue Manager queries on the async path when True.
        :return: ordered, de-duplicated candidate service URIs.
        """
        uris = []
        try:
            manager_uris = self.discover_manager_ids() or []
        except Exception:
            manager_uris = []
        for manager_uri in manager_uris:
            clean_manager = manager_uri.rstrip("/")
            manager = self._get(clean_manager, do_async)
            linked = self._dell_oem_link(manager)
            if linked:
                uris.append(linked.rstrip("/"))
            uris.append(f"{clean_manager}/Oem/Dell/{_SERVICE_NAME}")
        uris.append(_DEFAULT_SERVICE_URI)
        return list(dict.fromkeys(uri for uri in uris if uri))

    def _metadata(self, do_async, resource_uri=None):
        """Return InitializeMedia target metadata.

        :param do_async: issue Redfish reads on the async path when True.
        :param resource_uri: optional direct DellPersistentStorageService URI.
        :return: CommandResult with target metadata, or an action-not-found error.
        """
        checked = []
        uris = [resource_uri.rstrip("/")] if resource_uri else self._service_uris(do_async)
        for uri in dict.fromkeys(uris):
            service = self._get(uri, do_async)
            if not service:
                checked.append(uri)
                continue
            actions = self.discover_redfish_actions(self, service)
            target = self._flatten_action_targets(service).get(
                _INITIALIZE_MEDIA_ACTION
            )
            if target:
                return CommandResult(
                    {
                        "persistent_storage_service": uri,
                        "action": _INITIALIZE_MEDIA_ACTION,
                        "target": target,
                        "level": classify(_INITIALIZE_MEDIA_ACTION).value,
                    },
                    actions,
                    None,
                    None,
                )
            checked.append(uri)
        return CommandResult(
            {
                "action": _INITIALIZE_MEDIA_ACTION,
                "checked": checked,
            },
            None,
            None,
            f"action '{_INITIALIZE_MEDIA_ACTION}' not found",
        )

    def execute(self,
                resource_uri: Optional[str] = None,
                confirm: Optional[bool] = False,
                confirm_irreversible: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Preview or invoke DellPersistentStorageService.InitializeMedia.

        :param resource_uri: optional DellPersistentStorageService URI selector.
        :param confirm: authorize the POST when paired with irreversible consent.
        :param confirm_irreversible: acknowledge that initialization can erase data.
        :param dry_run: force preview mode even when confirmation is supplied.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue Redfish reads/POST on the async path when True.
        :return: CommandResult with target metadata, preview, or POST result.
        """
        metadata = self._metadata(bool(do_async), resource_uri)
        if metadata.error is not None:
            return metadata
        return self.invoke_action(
            metadata.data["persistent_storage_service"],
            "InitializeMedia",
            payload={},
            full_action_type=_INITIALIZE_MEDIA_ACTION,
            do_async=bool(do_async),
            expected_status=202,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
            confirm_irreversible=bool(confirm_irreversible),
        )
