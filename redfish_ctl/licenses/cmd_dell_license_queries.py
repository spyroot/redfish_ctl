"""Query Dell OEM license management actions.

    redfish_ctl dell-license-queries
    redfish_ctl dell-license-queries --query show-license-bits
    redfish_ctl dell-license-queries --query show-license-bits --dry_run

The command discovers ``#DellLicenseManagementService.ShowLicenseBits`` from
the Manager OEM Dell license-management link. With no query selected it lists
the discovered query target and adjacent license-management actions. The
selected query is read-only but is carried over POST, so it only runs when
``--query`` is explicit.

Author Mus spyroot@gmail.com
"""
import asyncio
import json
from abc import abstractmethod
from typing import Optional

from ..actions.action_policy import classify
from ..cmd_exceptions import InvalidArgument
from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton
from ..redfish_shared import RedfishApi

_SHOW_LICENSE_BITS_ACTION = "#DellLicenseManagementService.ShowLicenseBits"
_LICENSE_MANAGEMENT_FALLBACK = (
    f"{RedfishApi.Version}/Managers/iDRAC.Embedded.1/Oem/Dell/"
    "DellLicenseManagementService"
)
_QUERY_ALIASES = {
    "show-license-bits": ("ShowLicenseBits", _SHOW_LICENSE_BITS_ACTION),
}


class DellLicenseQueries(RedfishManagerBase,
                         scm_type=ApiRequestType.DellLicenseQueries,
                         name="dell-license-queries",
                         metaclass=Singleton):
    """Discover and run read-only Dell license-management queries."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-license-queries command."""
        super(DellLicenseQueries, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``dell-license-queries`` subcommand.

        :param cls: command class supplying the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--query",
            required=False,
            dest="query",
            type=str,
            choices=sorted(_QUERY_ALIASES),
            default=None,
            help="read-only license-management query to POST; omit to list targets",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="resolve the selected query target without POSTing",
        )
        return (
            cmd_parser,
            "dell-license-queries",
            "command fetch Dell OEM license-management query results",
        )

    @staticmethod
    def _link(data, key):
        """Return a Redfish link target from a ``{key: {@odata.id}}`` property.

        :param data: resource body containing the link.
        :param key: property name whose ``@odata.id`` to extract.
        :return: the linked URI, or None when absent or malformed.
        """
        link = (data or {}).get(key)
        return link.get("@odata.id") if isinstance(link, dict) else None

    @classmethod
    def _dell_oem_link(cls, manager, key):
        """Return an OEM Dell link from a Manager resource.

        :param manager: Manager resource body.
        :param key: OEM Dell link name.
        :return: the linked URI, or None when absent.
        """
        links = (manager or {}).get("Links", {})
        if not isinstance(links, dict):
            return None
        oem_links = links.get("Oem", {})
        if not isinstance(oem_links, dict):
            return None
        dell_links = oem_links.get("Dell", {})
        if not isinstance(dell_links, dict):
            return None
        return cls._link(dell_links, key)

    def _manager_link(self, key, do_async):
        """Return a Dell OEM link from the first Manager that advertises it.

        :param key: OEM Dell link name to resolve.
        :param do_async: issue Manager queries over the async Redfish path.
        :return: linked URI, or None when no Manager advertises it.
        """
        try:
            manager_uris = self.discover_manager_ids() or []
        except Exception:
            manager_uris = []
        for manager_uri in manager_uris:
            try:
                manager = self.base_query(
                    manager_uri.rstrip("/"),
                    do_async=do_async,
                ).data or {}
            except Exception:
                continue
            target = self._dell_oem_link(manager, key)
            if target:
                return target
        return None

    def _license_management_uri(self, do_async):
        """Resolve DellLicenseManagementService from Manager OEM links.

        :param do_async: issue Manager queries over the async Redfish path.
        :return: discovered license-management service URI, or the Dell fallback.
        """
        return (
            self._manager_link("DellLicenseManagementService", do_async)
            or _LICENSE_MANAGEMENT_FALLBACK
        )

    def _license_collection_uri(self, do_async):
        """Resolve the DellLicense collection link when advertised.

        :param do_async: issue Manager queries over the async Redfish path.
        :return: DellLicense collection URI, or None when absent.
        """
        return self._manager_link("DellLicenseCollection", do_async)

    def _license_management_service(self, do_async):
        """Read the DellLicenseManagementService resource.

        :param do_async: issue the query over the async Redfish path.
        :return: tuple of ``(uri, body)`` or ``(uri, CommandResult)`` on error.
        """
        uri = self._license_management_uri(do_async)
        try:
            data = self.base_query(uri, do_async=do_async).data or {}
        except Exception as exc:
            return uri, CommandResult(None, None, None, f"failed to read {uri}: {exc}")
        return uri, data

    @staticmethod
    def _action_details(actions, targets):
        """Return sorted metadata for advertised license-management actions.

        :param actions: discovered short-name action map.
        :param targets: full action type to target URI map.
        :return: list of JSON-serializable action metadata dictionaries.
        """
        rows = []
        for full_action, target in sorted(targets.items()):
            short = full_action.rsplit(".", 1)[-1].lstrip("#")
            action = actions.get(short)
            args = getattr(action, "args", None) or {}
            rows.append({
                "name": short,
                "action": full_action,
                "target": target,
                "level": classify(full_action).value,
                "parameters": {
                    key: sorted(values or [])
                    for key, values in sorted(args.items())
                },
            })
        return rows

    def _query_metadata(self, do_async):
        """Return discovered Dell license-management query metadata.

        :param do_async: issue the license-management query over the async path.
        :return: CommandResult with target metadata or an error if absent.
        """
        uri, service = self._license_management_service(do_async)
        if isinstance(service, CommandResult):
            return service
        actions = self.discover_redfish_actions(self, service)
        targets = self._flatten_action_targets(service)
        target = targets.get(_SHOW_LICENSE_BITS_ACTION)
        if target is None:
            available = sorted(set(list(actions.keys()) + list(targets.keys())))
            return CommandResult(
                {
                    "license_management_service": uri,
                    "action": _SHOW_LICENSE_BITS_ACTION,
                    "available": available,
                },
                actions,
                None,
                f"action '{_SHOW_LICENSE_BITS_ACTION}' not found on {uri}",
            )
        return CommandResult(
            {
                "license_management_service": uri,
                "license_collection": self._license_collection_uri(do_async),
                "queries": {
                    "show-license-bits": {
                        "action": _SHOW_LICENSE_BITS_ACTION,
                        "target": target,
                        "level": classify(_SHOW_LICENSE_BITS_ACTION).value,
                    },
                },
                "actions": self._action_details(actions, targets),
            },
            actions,
            None,
            None,
        )

    @staticmethod
    def _normalize_query(query):
        """Normalize a selected query alias.

        :param query: query alias from the CLI or programmatic invocation.
        :return: canonical query alias, or None when no query was selected.
        :raises InvalidArgument: when an unsupported query is provided.
        """
        if query is None:
            return None
        key = query.strip().lower()
        if not key:
            raise InvalidArgument("query cannot be empty")
        if key not in _QUERY_ALIASES:
            allowed = ", ".join(sorted(_QUERY_ALIASES))
            raise InvalidArgument(f"unsupported Dell license query '{query}'; allowed: {allowed}")
        return key

    def _post_query(self, query, target, do_async):
        """POST a read-only Dell license-management query and keep the body.

        :param query: canonical query alias.
        :param target: discovered action target.
        :param do_async: issue the POST over the async Redfish path.
        :return: CommandResult with the response payload and action metadata.
        """
        _, full_action = _QUERY_ALIASES[query]
        headers = dict(self.json_content_type)
        url = f"{self._default_method}{self.redfish_ip}{target}"
        try:
            if do_async:
                loop = asyncio.get_event_loop()
                api_resp, response = loop.run_until_complete(
                    self.api_async_post_until_complete(
                        url,
                        json.dumps({}),
                        headers,
                        expected=200,
                    )
                )
            else:
                response = self.api_post_call(url, json.dumps({}), headers)
                api_resp = self.default_post_success(response, expected=200)
        except Exception as exc:
            return CommandResult(
                {
                    "query": query,
                    "action": full_action,
                    "target": target,
                    "payload": {},
                    "level": classify(full_action).value,
                },
                None,
                None,
                f"failed to POST {target}: {exc}",
            )

        try:
            response_body = response.json()
        except Exception:
            response_body = {}
        data = response_body if isinstance(response_body, dict) else {
            "response": response_body,
        }
        data.setdefault("Status", self.api_success_msg(api_resp)["Status"])
        data.setdefault("executed", True)
        data.setdefault("method", "POST")
        data.setdefault("query", query)
        data.setdefault("action", full_action)
        data.setdefault("target", target)
        data.setdefault("level", classify(full_action).value)
        return CommandResult(data, None, None, None)

    def execute(self,
                query: Optional[str] = None,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List Dell license-management targets, or run a selected query.

        :param query: selected read-only query alias; None lists target metadata.
        :param dry_run: resolve the selected query without POSTing.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue the underlying query and POST on the async path.
        :return: a CommandResult with metadata, dry-run preview, or query result.
        :raises InvalidArgument: when ``query`` is empty or unsupported.
        """
        selected = self._normalize_query(query)
        metadata = self._query_metadata(do_async)
        if selected is None or metadata.error:
            return metadata

        query_meta = metadata.data["queries"][selected]
        target = query_meta["target"]
        _, full_action = _QUERY_ALIASES[selected]
        if dry_run:
            return CommandResult(
                {
                    "dry_run": True,
                    "query": selected,
                    "action": full_action,
                    "target": target,
                    "payload": {},
                    "level": classify(full_action).value,
                    "blocked": None,
                },
                metadata.discovered,
                None,
                None,
            )
        result = self._post_query(selected, target, bool(do_async))
        return CommandResult(result.data, metadata.discovered, None, result.error)
