"""Install a Redfish license through LicenseService.Install.

    redfish_ctl license-install
    redfish_ctl license-install --license-file-uri https://repo.example.test/license.xml --dry_run
    redfish_ctl license-install --license-file-uri https://repo.example.test/license.xml --confirm

The command resolves ``#LicenseService.Install`` from the service root's
LicenseService link. Installing a license changes BMC entitlement state, so the
action is DESTRUCTIVE: without ``--confirm`` the command only previews the POST.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton
from ..redfish_shared import RedfishApi

_LICENSE_INSTALL_ACTION = "#LicenseService.Install"


class LicenseInstall(RedfishManagerBase,
                     scm_type=ApiRequestType.LicenseInstall,
                     name="license-install",
                     metaclass=Singleton):
    """Install a license file through the Redfish LicenseService."""

    def __init__(self, *args, **kwargs):
        """Initialize the license-install command."""
        super(LicenseInstall, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded ``license-install`` subcommand.

        :param cls: command class supplying the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--license-file-uri",
            required=False,
            dest="license_file_uri",
            type=str,
            default=None,
            help="URI of the license file for LicenseService.Install; omit to "
                 "list the discovered action target without mutating",
        )
        cmd_parser.add_argument(
            "--transfer-protocol",
            required=False,
            dest="transfer_protocol",
            type=str,
            default=None,
            help="transfer protocol for the license file, such as HTTP or HTTPS",
        )
        cmd_parser.add_argument(
            "--username",
            required=False,
            dest="license_username",
            type=str,
            default=None,
            help="optional username for the license file URI",
        )
        cmd_parser.add_argument(
            "--password",
            required=False,
            dest="license_password",
            type=str,
            default=None,
            help="optional password for the license file URI",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            dest="confirm",
            default=False,
            help="fire the LicenseService.Install POST; without it the command previews",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="resolve the target and show it without POSTing; overrides --confirm",
        )
        return (
            cmd_parser,
            "license-install",
            "command install a Redfish license file",
        )

    @staticmethod
    def _link(data, key):
        """Return a Redfish link target from a ``{key: {@odata.id}}`` property.

        :param data: the resource body holding the link.
        :param key: property name whose ``@odata.id`` to extract.
        :return: the link target URI, or None when absent or malformed.
        """
        link = (data or {}).get(key)
        return link.get("@odata.id") if isinstance(link, dict) else None

    def _license_service_uri(self, do_async):
        """Resolve the LicenseService URI from the service root.

        :param do_async: issue the service-root query over the async Redfish path.
        :return: the LicenseService ``@odata.id``, or the standard fallback URI.
        """
        try:
            root = self.base_query(RedfishApi.Version, do_async=do_async).data or {}
        except Exception:
            root = {}
        return self._link(root, "LicenseService") or f"{RedfishApi.Version}/LicenseService"

    def _license_service(self, do_async):
        """Read the LicenseService resource.

        :param do_async: issue the query over the async Redfish path.
        :return: tuple of ``(uri, body)``.
        """
        uri = self._license_service_uri(do_async)
        try:
            data = self.base_query(uri, do_async=do_async).data or {}
        except Exception as exc:
            return uri, CommandResult(None, None, None, f"failed to read {uri}: {exc}")
        return uri, data

    def _install_metadata(self, do_async):
        """Return discovered LicenseService.Install target metadata.

        :param do_async: issue the LicenseService query over the async Redfish path.
        :return: CommandResult with target metadata, or an error if absent.
        """
        uri, service = self._license_service(do_async)
        if isinstance(service, CommandResult):
            return service
        actions = self.discover_redfish_actions(self, service)
        target = self._flatten_action_targets(service).get(_LICENSE_INSTALL_ACTION)
        action = actions.get("Install")
        transfer_protocols = sorted((getattr(action, "args", {}) or {}).get(
            "TransferProtocol", []
        ))
        if target is None:
            available = sorted(set(list(actions.keys())
                                   + list(self._flatten_action_targets(service).keys())))
            return CommandResult(
                {
                    "license_service": uri,
                    "action": _LICENSE_INSTALL_ACTION,
                    "available": available,
                },
                actions,
                None,
                f"action '{_LICENSE_INSTALL_ACTION}' not found on {uri}",
            )
        return CommandResult(
            {
                "license_service": uri,
                "action": _LICENSE_INSTALL_ACTION,
                "target": target,
                "transfer_protocols": transfer_protocols,
            },
            actions,
            None,
            None,
        )

    @staticmethod
    def _payload(license_file_uri,
                 transfer_protocol=None,
                 license_username=None,
                 license_password=None):
        """Build a LicenseService.Install payload.

        :param license_file_uri: URI of the license file to install.
        :param transfer_protocol: optional TransferProtocol value.
        :param license_username: optional username for the URI.
        :param license_password: optional password for the URI.
        :return: JSON-serializable action payload.
        :raises InvalidArgument: when ``license_file_uri`` is empty.
        """
        uri = (license_file_uri or "").strip()
        if not uri:
            raise InvalidArgument("license file URI cannot be empty")
        payload = {
            "LicenseFileURI": uri,
            "TransferProtocol": transfer_protocol,
            "Username": license_username,
            "Password": license_password,
        }
        return {key: value for key, value in payload.items() if value is not None}

    @staticmethod
    def _redact_password(result):
        """Mask Password in dry-run payloads before returning to callers.

        :param result: CommandResult from ``invoke_action``.
        :return: CommandResult with any dry-run password masked.
        """
        if not isinstance(result.data, dict):
            return result
        payload = result.data.get("payload")
        if isinstance(payload, dict) and "Password" in payload:
            payload = dict(payload)
            payload["Password"] = "********"
            result.data["payload"] = payload
        return result

    def execute(self,
                license_file_uri: Optional[str] = None,
                transfer_protocol: Optional[str] = None,
                license_username: Optional[str] = None,
                license_password: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List the install action target, or install a license file.

        With no ``--license-file-uri`` the command returns the discovered
        LicenseService.Install target WITHOUT mutating. With a URI it resolves
        and invokes the action; because the action is DESTRUCTIVE, the POST only
        fires with ``--confirm``. ``--dry_run`` remains a no-POST override even
        when ``--confirm`` is also set.

        :param license_file_uri: URI of the license file to install; None lists
            target metadata.
        :param transfer_protocol: optional TransferProtocol value.
        :param license_username: optional username for the license file URI.
        :param license_password: optional password for the license file URI.
        :param confirm: authorize the LicenseService.Install POST to actually fire.
        :param dry_run: resolve the target and show the payload without POSTing.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue the underlying query and POST on the async path.
        :return: a CommandResult with target metadata, the install outcome, or a
            blocked/dry-run preview.
        :raises InvalidArgument: when ``license_file_uri`` is empty after trimming.
        """
        if license_file_uri is None:
            return self._install_metadata(do_async)

        result = self.invoke_action(
            self._license_service_uri(do_async),
            "Install",
            payload=self._payload(
                license_file_uri,
                transfer_protocol=transfer_protocol,
                license_username=license_username,
                license_password=license_password,
            ),
            full_action_type=_LICENSE_INSTALL_ACTION,
            do_async=do_async,
            expected_status=202,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
        )
        return self._redact_password(result)
