"""Redfish discovery command

Command discover all idrac / redfish resources.

    redfish_ctl discovery
    redfish_ctl discovery --network 192.168.254.0/24

Author Mus spyroot@gmail.com
"""
import json
import os
import re
import time
from abc import abstractmethod
from pathlib import Path
from typing import Optional

import requests

from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult
from ..redfish_shared import env_first

# Upper bound on how deep recursive_discovery will walk below a top-level
# resource. Real Redfish trees are far shallower than this; the bound exists
# purely to guarantee termination even if normalization/dedup ever misses a
# cyclic back-reference.
DEFAULT_DISCOVERY_MAX_DEPTH = 32

# Matches an individual *member* of any LogService's Entries collection
# (``/LogServices/<Sel|Journal|Lclog|...>/Entries/<entry-id>``), across vendors.
# These entries are transient, high-cardinality log lines with no structural
# value to the crawl or the igc RL map; enumerating them one GET at a time
# overloads the BMC — observed live on GB300 HGX BMCs as an SSL "UNEXPECTED_EOF"
# storm over thousands of Journal entries. The crawl keeps the ``.../Entries``
# collection index itself (which does not match, having nothing after
# ``/Entries/``) and skips only the members unless --include-log-entries is set.
LOG_ENTRY_MEMBER_RE = re.compile(r"/LogServices/[^/]+/Entries/.+")


class Discovery(RedfishManagerBase,
                scm_type=ApiRequestType.Discovery,
                name='discovery',
                metaclass=Singleton):
    """A command discovery all redfish resource  based on a resource path
    """

    def __init__(self, *args, **kwargs):
        """

        :param args:
        :param kwargs:
        """
        super(Discovery, self).__init__(*args, **kwargs)
        self._discovered_url_file_mapping = {}
        self._api_allowed_methods = {}
        # Original HTTP status for EVERY URL the crawl touched (2xx and errors),
        # and the saved body file for each non-2xx response. These make the crawl
        # lossless: the corpus keeps the exact status + body the BMC returned, so
        # a sim can replay a 404/405/400 faithfully instead of the crawl silently
        # dropping it (as it did before). Both are written into rest_api_map.npy.
        self._http_status = {}
        self._error_file_mapping = {}

        self.visited_urls = {}
        home_dir = str(Path.home())
        redfish_ip = self.redfish_ip.replace(":", "")
        self.json_response_dir = f"{str(home_dir)}/.json_responses/{redfish_ip}"

        # default filter that will skip these entities.
        self.default_query_filter = [
            "LogServices/Sel/Entries",
            "JID_",
            "StdSecbootpolicy.",
            "Signatures/StdSecbootpolicy.",
            "Lclog/Entries/"]

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command and all optional flags.

        Two modes: with ``--network`` it scans a segment for Redfish BMCs (a
        credential-less sweep, shared with ``bmc-scan``); without it, it deep
        crawls the single configured host's Redfish tree.
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            '--network', required=False, dest='scan_network', type=str, default=None,
            help="scan a network segment (CIDR, e.g. 192.168.254.0/24) for Redfish "
                 "BMCs instead of crawling one host; needs no idrac_ip/credentials")
        cmd_parser.add_argument(
            '--port', required=False, dest='scan_port', type=int, default=443,
            help="with --network: HTTPS port to probe (default 443)")
        cmd_parser.add_argument(
            '--timeout', required=False, dest='scan_timeout', type=float, default=2.0,
            help="with --network: per-host probe timeout in seconds (default 2)")
        cmd_parser.add_argument(
            '--workers', required=False, dest='scan_workers', type=int, default=64,
            help="with --network: concurrent probes (default 64)")
        cmd_parser.add_argument(
            '--include-log-entries', action='store_true', dest='include_log_entries',
            default=False,
            help="also crawl individual LogServices/*/Entries members (SEL, "
                 "Journal, Lclog...). Off by default: entries are transient, "
                 "high-cardinality, and enumerating them can overload a BMC "
                 "(SSL EOF) with no structural value. The Entries collection "
                 "index is always captured.")
        help_text = "command discovery all action (or --network CIDR to scan a segment)."
        return cmd_parser, "discovery", help_text

    def extract_odata_ids(self, data):
        """Yield Redfish reference strings (``@odata.id`` / ``Uri``) found in ``data``.

        Only string values are yielded. Inside a JsonSchemas document the
        ``@odata.id`` key is a *property definition* (e.g.
        ``{"$ref": ".../odata-v4.json#/definitions/id"}``), not an actual
        reference; yielding that dict would crash the downstream walk
        (``normalize_resource_path`` / membership checks expect a path string).
        The ``isinstance`` guard keeps discovery robust across vendors whose
        crawl reaches schema content (seen live on Supermicro).

        :param data: a parsed Redfish document (dict/list) or any nested value.
        :return: yields reference path strings.
        """
        if isinstance(data, dict):
            odata_id = data.get("@odata.id")
            if isinstance(odata_id, str):
                yield odata_id
            uri = data.get("Uri")
            if isinstance(uri, str):
                yield uri
            for value in data.values():
                yield from self.extract_odata_ids(value)
        elif isinstance(data, list):
            for item in data:
                yield from self.extract_odata_ids(item)

    @staticmethod
    def normalize_resource_path(resource_path: str) -> str:
        """Canonicalize a Redfish resource path so URI variants map to one key.

        Redfish hands back the same logical resource under several spellings:
        a trailing slash (``.../Managers/``), a query string from an ``$expand``
        / ``$select`` / ``$ref`` request, or duplicate slashes from a naive
        string join (``/redfish//v1``). Keying ``visited_urls`` on the raw
        string would treat each spelling as a new resource and re-walk it, so we
        collapse them to a single canonical form first.

        :param resource_path: a raw ``@odata.id`` / ``Uri`` value
        :return: the path with any query string dropped, duplicate slashes
                 collapsed, and a trailing slash removed (never reduced to "")
        """
        if not resource_path:
            return resource_path
        # Drop any query string ($expand, $select, $ref, odata.id fragments...).
        path = resource_path.split("?", 1)[0]
        path = path.split("#", 1)[0]
        # Collapse duplicate slashes ("/redfish//v1/Managers" -> "/redfish/v1/Managers").
        while "//" in path:
            path = path.replace("//", "/")
        # Strip trailing slash(es), but never collapse the path away entirely.
        if len(path) > 1:
            path = path.rstrip("/") or path
        return path

    def _query_with_retry(self, resource_path):
        """``base_query`` with bounded backoff retries on transient transport errors.

        Fragile BMCs (seen live on GB300 HGX baseboards) drop HTTPS connections
        under a sustained crawl -- SSL ``UNEXPECTED_EOF`` / urllib3 "Max retries
        exceeded" -- because every GET opens a fresh TLS connection. Those are
        transient: a short backoff lets the BMC's HTTPS server recover, so we
        retry instead of losing the resource (the old behaviour silently skipped
        it). Non-transport failures (403/404, JSON errors) are *not* retried --
        they are not ``requests.RequestException`` and propagate to the caller's
        existing handlers unchanged.

        Tunable via env: ``REDFISH_DISCOVERY_RETRIES`` (default 4),
        ``REDFISH_DISCOVERY_BACKOFF`` seconds (default 2.0, exponential), and
        ``REDFISH_DISCOVERY_PACE_MS`` (default 0) to throttle the request rate and
        be gentle on a fragile BMC (legacy ``IDRAC_DISCOVERY_*`` still honored).

        :param resource_path: canonical Redfish resource path to fetch.
        :return: the ``base_query`` CommandResult.
        :raises: the last transport exception if all attempts fail.
        """
        attempts = max(1, int(env_first(
            "REDFISH_DISCOVERY_RETRIES", "IDRAC_DISCOVERY_RETRIES", default="4")))
        backoff = float(env_first(
            "REDFISH_DISCOVERY_BACKOFF", "IDRAC_DISCOVERY_BACKOFF", default="2.0"))
        pace = float(env_first(
            "REDFISH_DISCOVERY_PACE_MS", "IDRAC_DISCOVERY_PACE_MS", default="0")) / 1000.0
        last_exc = None
        for attempt in range(attempts):
            try:
                result = self.base_query(resource_path)
                if pace:
                    time.sleep(pace)
                return result
            except requests.exceptions.RequestException as exc:
                last_exc = exc
                if attempt + 1 < attempts:
                    time.sleep(backoff * (2 ** attempt))
        raise last_exc

    def _fetch_capture(self, resource_path):
        """Fetch ``resource_path`` and return ``(status_code, body, allow_header)``
        WITHOUT raising on an HTTP error status.

        ``base_query`` runs ``default_error_handler`` and raises on any non-2xx, so
        the old crawl lost the body and status of every 404/405/403 (real ground
        truth a sim needs). This goes straight to ``api_get_call`` (the raw
        ``requests.Response``) so the ORIGINAL status code and body are always
        captured. Only transport failures raise; they are retried with the same
        bounded backoff as ``_query_with_retry`` and re-raised if all attempts fail.

        The body is the parsed JSON when the response is JSON; a non-JSON body is
        preserved verbatim as ``{"_raw_text": <text>}`` so nothing is discarded.

        :param resource_path: canonical Redfish resource path to fetch.
        :return: ``(int status_code, body, Optional[str] allow_header)``.
        :raises: the last transport exception if every attempt fails.
        """
        attempts = max(1, int(env_first(
            "REDFISH_DISCOVERY_RETRIES", "IDRAC_DISCOVERY_RETRIES", default="4")))
        backoff = float(env_first(
            "REDFISH_DISCOVERY_BACKOFF", "IDRAC_DISCOVERY_BACKOFF", default="2.0"))
        pace = float(env_first(
            "REDFISH_DISCOVERY_PACE_MS", "IDRAC_DISCOVERY_PACE_MS", default="0")) / 1000.0
        req = f"{self._default_method}{self.redfish_ip}{resource_path}"
        last_exc = None
        for attempt in range(attempts):
            try:
                response = self.api_get_call(req, {})
                if pace:
                    time.sleep(pace)
                try:
                    body = response.json()
                except ValueError:
                    body = {"_raw_text": response.text}
                return response.status_code, body, response.headers.get("Allow")
            except requests.exceptions.RequestException as exc:
                last_exc = exc
                if attempt + 1 < attempts:
                    time.sleep(backoff * (2 ** attempt))
        raise last_exc

    def recursive_discovery(self,
                            resource_path,
                            depth: int = 0,
                            max_depth: int = DEFAULT_DISCOVERY_MAX_DEPTH):
        """Recursively walk and discover Redfish resources from ``resource_path``.

        URI variants of the same resource (trailing slash, query string,
        duplicate slashes) are normalized to one key before the visited check,
        so each logical resource is fetched exactly once. Recursion stops once
        ``depth`` exceeds ``max_depth`` so a missed back-reference can never spin
        forever.

        :param resource_path: a Redfish resource path (raw ``@odata.id``)
        :param depth: current recursion depth; callers start at 0
        :param max_depth: maximum depth to walk below the starting resource
        :return:
        """
        if depth > max_depth:
            return

        resource_path = self.normalize_resource_path(resource_path)

        for query_filter in self.default_query_filter:
            if query_filter in resource_path:
                self.visited_urls[resource_path] = True
                return

        # Skip individual log-entry members (any vendor's LogService) unless the
        # caller opted in with --include-log-entries. Defaults to skipping so the
        # crawl never grinds thousands of transient entries and overloads the BMC
        # (see LOG_ENTRY_MEMBER_RE). The parent ``.../Entries`` collection index
        # is still captured; only its members are skipped.
        if getattr(self, "_skip_log_entries", True) and LOG_ENTRY_MEMBER_RE.search(resource_path):
            self.visited_urls[resource_path] = True
            return

        if resource_path in self.visited_urls or not resource_path.startswith("/redfish/v1"):
            return

        try:
            status, body, allow_header = self._fetch_capture(resource_path)
        except requests.exceptions.RequestException as exc:
            # Transport failure after all retries: the BMC was unreachable for this
            # URL. Record the attempt as status 0 so the crawl notes it, then move
            # on (there is no body to save).
            self.visited_urls[resource_path] = True
            self._http_status[resource_path] = 0
            print("Discovery transport error at {}: {}".format(resource_path, exc))
            return
        except Exception as other_err:
            self.visited_urls[resource_path] = True
            print("Discovery error at {}: {}".format(resource_path, other_err))
            return

        allowed_methods = (
            [method.strip() for method in allow_header.split(",")]
            if allow_header is not None else [])
        self.visited_urls[resource_path] = True
        self._http_status[resource_path] = status

        # ALWAYS persist the original response body — success AND error — so the
        # corpus/sim keeps the exact bytes and status the BMC returned. A non-2xx
        # body is saved under a ``.error.json`` name so a URL-keyed replay consumer
        # (mock BMC) never mistakes a captured 404/405 envelope for a live 200.
        is_ok = 200 <= status < 300
        suffix = ".json" if is_ok else ".error.json"
        response_filename = os.path.join(
            self.json_response_dir, resource_path.replace("/", "_") + suffix)
        with open(response_filename, "w") as file:
            json.dump(body, file, indent=4)
        self._api_allowed_methods[resource_path] = allowed_methods

        if is_ok:
            print("Discovery: {} {} {}".format(resource_path, status, allowed_methods))
            self._discovered_url_file_mapping[resource_path] = response_filename
            for r in self.extract_odata_ids(body):
                self.recursive_discovery(r, depth + 1, max_depth)
        else:
            # A non-2xx (404/405/400/403...) is real ground truth for the sim.
            # Keep its body + status, but do NOT recurse into an error envelope.
            print("Discovery: {} {} (error captured)".format(resource_path, status))
            self._error_file_mapping[resource_path] = response_filename

    def save_url_file_mapping(self):
        """Save the URL-to-file mapping to a JSON respond file
        and what each api allow.

        numpy is imported lazily here so importing this module does not
        require numpy to be installed; it is only needed for ``np.save``.
        """
        import numpy as np

        filename = os.path.join(self.json_response_dir, "rest_api_map.npy")
        mappings = {
            "url_file_mapping": self._discovered_url_file_mapping,
            "allowed_methods_mapping": self._api_allowed_methods,
            # New, additive: original status for every touched URL, and the saved
            # body file for each captured error. Consumers that only read the two
            # legacy keys are unaffected.
            "http_status_mapping": self._http_status,
            "error_file_mapping": self._error_file_mapping,
        }
        filename = os.path.join(self.json_response_dir, filename)
        np.save(filename, mappings)

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                scan_network: Optional[str] = None,
                scan_port: Optional[int] = 443,
                scan_timeout: Optional[float] = 2.0,
                scan_workers: Optional[int] = 64,
                include_log_entries: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Executes discovery action command
        python redfish_ctl discovery
        python redfish_ctl discovery --network 192.168.254.0/24

        With ``scan_network`` set, this is a credential-less segment scan for
        Redfish BMCs (returns a list of {IP, Vendor, Product, RedfishVersion,
        Auth}); it never crawls the single host and never writes the ``.npy``
        rest-api map. Without it, the original single-host deep crawl runs
        unchanged.

        :param scan_network: CIDR to scan for BMCs instead of crawling one host.
        :param scan_port: with ``scan_network``, HTTPS port to probe.
        :param scan_timeout: with ``scan_network``, per-host probe timeout (s).
        :param scan_workers: with ``scan_network``, concurrent probes.
        :param include_log_entries: also crawl individual LogServices/*/Entries
               members (off by default; enumerating them can overload a BMC).
        :param do_async: note async will subscribe to an event loop.
        :param do_expanded:  will do expand query
        :param filename: if filename indicate call will save the response to this file.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :return: CommandResult and if filename provide will save to a file.
        """

        # Network-scan mode: find BMCs on a segment, no target host or creds.
        if scan_network:
            from ..cmd_utils import save_if_needed
            from .net_scan import scan_segment
            try:
                found = scan_segment(scan_network, scan_port, scan_timeout, scan_workers)
            except ValueError as ve:
                return CommandResult([], None, None, f"invalid network '{scan_network}': {ve}")
            save_if_needed(filename, found)
            return CommandResult(found, None, None, None)

        # Off by default -> recursive_discovery skips individual log-entry members
        # (see LOG_ENTRY_MEMBER_RE) so the crawl does not grind thousands of
        # transient entries and overload the BMC.
        self._skip_log_entries = not include_log_entries

        os.makedirs(self.json_response_dir, exist_ok=True)
        if not os.path.isdir(self.json_response_dir):
            raise ValueError("Failed to create directory: {}".format(self.json_response_dir))

        result = self.base_query("/redfish/v1/",
                                 filename=filename,
                                 do_async=do_async,
                                 do_expanded=do_expanded)

        # Persist the ServiceRoot itself. base_query fetches it only to seed the
        # crawl, so historically it was never written to disk or mapped — the one
        # resource every consumer needs was missing. Save it, map it, and record
        # its status like any other resource (it is a successful 2xx GET here).
        service_root_key = "/redfish/v1/"
        service_root_file = os.path.join(self.json_response_dir, "_redfish_v1.json")
        with open(service_root_file, "w") as file:
            json.dump(result.data, file, indent=4)
        self._discovered_url_file_mapping[service_root_key] = service_root_file
        self._api_allowed_methods[service_root_key] = (
            [m.strip() for m in result.extra.split(",")] if result.extra else [])
        self._http_status[service_root_key] = 200

        # Seed normalized keys so a child link back to the root (e.g. the
        # bare "/redfish/v1") matches and is not re-walked.
        self.visited_urls[self.normalize_resource_path("/redfish/v1/")] = True
        self.visited_urls[self.normalize_resource_path("/redfish/v1/CompositionService")] = True
        odata_ids = list(self.extract_odata_ids(result.data))
        for r in odata_ids:
            self.recursive_discovery(r)
        self.save_url_file_mapping()
        return result
