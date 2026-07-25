"""Test Dell Lifecycle Controller network-share reachability.

    redfish_ctl dell-lc-network-share-test
    redfish_ctl dell-lc-network-share-test --host repo.example.test --dry_run
    redfish_ctl dell-lc-network-share-test --host repo.example.test --confirm

The command resolves ``#DellLCService.TestNetworkShare`` from the Dell
Lifecycle Controller service. The action can send test traffic from the BMC, so
it previews by default and only POSTs when ``--confirm`` is provided.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton, TestNetworkShareReq
from ..redfish_manager import CommandResult

_TEST_NETWORK_SHARE_ACTION = "#DellLCService.TestNetworkShare"


def _link(data, key):
    """Return an ``@odata.id`` link from a Redfish object.

    :param data: Redfish resource body.
    :param key: property name to inspect.
    :return: linked URI, or None when absent.
    """
    link = data.get(key) if isinstance(data, dict) else None
    return link.get("@odata.id") if isinstance(link, dict) else None


class DellLcNetworkShareTest(IDracManager,
                             scm_type=ApiRequestType.DellLcNetworkShareTest,
                             name="dell-lc-network-share-test",
                             metaclass=Singleton):
    """Preview or invoke DellLCService.TestNetworkShare."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-lc-network-share-test command."""
        super(DellLcNetworkShareTest, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``dell-lc-network-share-test`` subcommand.

        :param cls: command class supplying the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--host",
            required=False,
            type=str,
            default=None,
            help="network share host/IP to test; omit to list the action target",
        )
        cmd_parser.add_argument(
            "--share-type",
            required=False,
            dest="share_type",
            type=str,
            default="HTTPS",
            help="DellLCService.TestNetworkShare ShareType value",
        )
        cmd_parser.add_argument(
            "--proxy-support",
            required=False,
            dest="proxy_support",
            type=str,
            default="Off",
            help="ProxySupport value, such as Off or ParametersProxy",
        )
        cmd_parser.add_argument(
            "--ignore-cert-warning",
            required=False,
            dest="ignore_cert_warning",
            type=str,
            default="On",
            help="IgnoreCertWarning value, usually On or Off",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            default=False,
            help="POST the TestNetworkShare action instead of previewing it",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="resolve the target and payload without POSTing",
        )
        return (
            cmd_parser,
            "dell-lc-network-share-test",
            "command test Dell Lifecycle Controller network-share reachability",
        )

    def _get(self, uri, do_async):
        """GET a Redfish resource, returning an empty dict on optional misses.

        :param uri: Redfish URI to read.
        :param do_async: issue the read on the async path when True.
        :return: resource body, or an empty dict when missing/unreadable.
        """
        try:
            data = self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _dell_oem_links(manager):
        """Return the ``Links.Oem.Dell`` block from a Manager resource.

        :param manager: Redfish Manager resource body.
        :return: Dell OEM link block, or an empty dict.
        """
        links = manager.get("Links") if isinstance(manager, dict) else None
        oem = links.get("Oem") if isinstance(links, dict) else None
        dell = oem.get("Dell") if isinstance(oem, dict) else None
        return dell if isinstance(dell, dict) else {}

    def _candidate_service_uris(self, do_async):
        """Return DellLCService URI candidates in discovery-first order.

        :param do_async: issue supporting reads on the async path when True.
        :return: de-duplicated candidate DellLCService URIs.
        """
        candidates = []
        for manager_uri in self.discover_manager_ids() or []:
            manager = self._get(manager_uri, do_async)
            service_uri = _link(self._dell_oem_links(manager), "DellLCService")
            if service_uri:
                candidates.append(service_uri)
            candidates.append(f"{manager_uri.rstrip('/')}/Oem/Dell/DellLCService")
            candidates.append(f"{manager_uri.rstrip('/')}/DellLCService")
        candidates.append("/redfish/v1/Dell/Managers/iDRAC.Embedded.1/DellLCService")

        seen = set()
        ordered = []
        for uri in candidates:
            if uri and uri not in seen:
                seen.add(uri)
                ordered.append(uri)
        return ordered

    def _service_metadata(self, do_async):
        """Return metadata for the first service advertising TestNetworkShare.

        :param do_async: issue reads on the async path when True.
        :return: CommandResult with discovered metadata, or a not-found error.
        """
        checked = []
        for service_uri in self._candidate_service_uris(do_async):
            service = self._get(service_uri, do_async)
            if not service:
                checked.append(service_uri)
                continue
            actions = self.discover_redfish_actions(self, service)
            target = self._flatten_action_targets(service).get(
                _TEST_NETWORK_SHARE_ACTION
            )
            action = actions.get("TestNetworkShare")
            if target:
                return CommandResult(
                    {
                        "service": service_uri,
                        "action": _TEST_NETWORK_SHARE_ACTION,
                        "target": target,
                        "allowable_values": getattr(action, "args", {}) or {},
                    },
                    actions,
                    None,
                    None,
                )
            checked.append(service_uri)

        return CommandResult(
            {
                "action": _TEST_NETWORK_SHARE_ACTION,
                "checked": checked,
            },
            None,
            None,
            f"action '{_TEST_NETWORK_SHARE_ACTION}' not found",
        )

    @staticmethod
    def _payload(host, share_type, proxy_support, ignore_cert_warning):
        """Build a DellLCService.TestNetworkShare payload.

        :param host: share host/IP address to test.
        :param share_type: Dell ShareType value.
        :param proxy_support: Dell ProxySupport value.
        :param ignore_cert_warning: Dell IgnoreCertWarning value.
        :return: JSON payload for the action.
        :raises InvalidArgument: when host is empty.
        """
        clean_host = (host or "").strip()
        if not clean_host:
            raise InvalidArgument("network share host cannot be empty")
        return TestNetworkShareReq(
            host=clean_host,
            share_type=(share_type or "").strip() or "HTTPS",
            proxy_support=(proxy_support or "").strip() or "Off",
            ignore_cert_warning=(ignore_cert_warning or "").strip() or "On",
        ).network_share_req

    def execute(self,
                host: Optional[str] = None,
                share_type: Optional[str] = "HTTPS",
                proxy_support: Optional[str] = "Off",
                ignore_cert_warning: Optional[str] = "On",
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List or invoke DellLCService.TestNetworkShare.

        :param host: share host/IP address to test; omitted means list metadata.
        :param share_type: Dell ShareType payload value.
        :param proxy_support: Dell ProxySupport payload value.
        :param ignore_cert_warning: Dell IgnoreCertWarning payload value.
        :param confirm: authorize the TestNetworkShare POST.
        :param dry_run: force preview mode even when ``confirm`` is true.
        :param filename: accepted for CLI compatibility; not used.
        :param data_type: accepted for CLI compatibility; not used.
        :param verbose: accepted for CLI compatibility; not used.
        :param do_async: issue underlying reads/POST on the async path.
        :return: CommandResult with metadata, dry-run preview, or POST result.
        """
        metadata = self._service_metadata(bool(do_async))
        if metadata.error:
            return metadata
        if host is None:
            return metadata

        return self.invoke_action(
            metadata.data["service"],
            "TestNetworkShare",
            payload=self._payload(
                host,
                share_type=share_type,
                proxy_support=proxy_support,
                ignore_cert_warning=ignore_cert_warning,
            ),
            full_action_type=_TEST_NETWORK_SHARE_ACTION,
            do_async=do_async,
            expected_status=200,
            dry_run=bool(dry_run) or not bool(confirm),
            confirm=bool(confirm),
        )
