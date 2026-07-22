"""Redfish implementation based
on redfish specification.

https://www.dmtf.org/standards/REDFISH

Author Mus spyroot@gmail.com
"""

import asyncio
import collections
import contextvars
import copy
import functools
import logging
import re
import threading
from abc import abstractmethod
from contextlib import contextmanager
from functools import cached_property
from typing import Any, Callable, Dict, Hashable, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .cmd_exceptions import AuthenticationFailed, ResourceNotFound, TaskIdUnavailable
from .cmd_utils import save_if_needed
from .redfish_exceptions import (
    RedfishForbidden,
    RedfishMethodNotAllowed,
    RedfishNotAcceptable,
    RedfishUnauthorized,
)
from .redfish_query import RedfishQuery
from .redfish_respond import RedfishRespondMessage
from .redfish_respond_error import RedfishError
from .redfish_shared import (
    RedfishApi,
    RedfishApiRespond,
    RedfishJson,
    RedfishJsonMessage,
    RedfishJsonSpec,
    env_first,
)
from .telemetry import tracing

"""Each command encapsulate result in named tuple"""
CommandResult = collections.namedtuple("cmd_result",
                                       ("data", "discovered", "extra", "error"))


class RedfishResponseCache:
    """Per-operation cache for parsed read-only Redfish GET responses."""

    def __init__(self):
        """Initialize an empty thread-safe response cache."""
        self._condition = threading.Condition()
        self._values = {}
        self._inflight = set()

    @staticmethod
    def _clone(value):
        """Return an isolated copy of cached response data.

        :param value: cached value to isolate for the caller.
        :return: a deep copy of ``value``.
        """
        return copy.deepcopy(value)

    def get_or_load(
            self,
            key: Hashable,
            loader: Callable[[], tuple[Any, Any]]) -> tuple[Any, Any]:
        """Return cached data for ``key`` or load it once.

        Concurrent callers for the same key wait for the first loader instead
        of issuing duplicate BMC GETs. Returned payloads are copied so callers
        can annotate rows without mutating the cached response.

        :param key: immutable cache key for the exact GET request shape.
        :param loader: callable that returns ``(data, allow_header)``.
        :return: cached or freshly loaded ``(data, allow_header)``.
        """
        with self._condition:
            if key in self._values:
                return self._clone(self._values[key])
            while key in self._inflight:
                self._condition.wait()
                if key in self._values:
                    return self._clone(self._values[key])
            self._inflight.add(key)

        try:
            value = loader()
        except BaseException:
            with self._condition:
                self._inflight.discard(key)
                self._condition.notify_all()
            raise

        stored = self._clone(value)
        with self._condition:
            self._values[key] = stored
            self._inflight.discard(key)
            self._condition.notify_all()
            return self._clone(stored)


_REDFISH_RESPONSE_CACHE = contextvars.ContextVar(
    "redfish_response_cache", default=None)


def active_redfish_response_cache():
    """Return the cache active for the current call context, if any.

    :return: active RedfishResponseCache for this context, or None.
    """
    return _REDFISH_RESPONSE_CACHE.get()


@contextmanager
def redfish_response_cache_scope(cache):
    """Temporarily bind a response cache to the current call context.

    :param cache: RedfishResponseCache to bind while the context is active.
    :return: context manager yielding ``cache``.
    """
    token = _REDFISH_RESPONSE_CACHE.set(cache)
    try:
        yield cache
    finally:
        _REDFISH_RESPONSE_CACHE.reset(token)


class RedfishManager:

    @staticmethod
    def _event_loop() -> asyncio.AbstractEventLoop:
        """Return a usable event loop for a synchronous caller.

        ``asyncio.get_event_loop()`` used to create a loop implicitly when none existed. Python 3.12
        deprecated that and 3.14 removed it, so on 3.14 it raises RuntimeError and every async path in
        this client dies before sending anything. Creating the loop explicitly when there is none keeps
        one behaviour across 3.10 through 3.14.

        :return: the running loop when one exists, otherwise a new loop installed for this thread.
        :raises RuntimeError: never — the no-loop case is handled by creating one.
        """
        try:
            return asyncio.get_event_loop_policy().get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            return loop

    def __init__(self,
                 redfish_ip: Optional[str] = "",
                 redfish_username: Optional[str] = "root",
                 redfish_password: Optional[str] = "",
                 redfish_port: Optional[int] = 443,
                 insecure: Optional[bool] = True,
                 is_http: Optional[bool] = False,
                 x_auth: Optional[str] = None,
                 is_debug: Optional[bool] = False):
        """Default constructor for Redfish Manager.
           it requires a credentials to interact with redfish endpoint.
           By default, Redfish Manager uses json to serialize a data to callee
           and uses json content type.

        :param redfish_ip: redfish IP or hostname
        :param redfish_username: redfish username default is root
        :param redfish_password: redfish password.
        :param redfish_port: redfish TCP port (default 443); accepts an int or str.
        :param insecure: when True (the default) TLS certificate verification is
            skipped. BMCs ship self-signed certificates, so verification is
            opt-in: pass ``insecure=False`` to verify the server certificate.
        :param is_http: use plain HTTP instead of HTTPS for requests when True.
        :param x_auth: X-Authentication header.
        :param is_debug: when True, include exception tracebacks in error logs.
        """
        self._redfish_ip = redfish_ip
        self._username = redfish_username
        self._password = redfish_password

        if isinstance(redfish_port, str):
            redfish_port = int(redfish_port)

        self._port = redfish_port
        # ``insecure`` means "skip TLS verification"; requests' ``verify`` is the
        # inverse, so verification is enabled only when insecure is explicitly off.
        self._is_verify_cert = not insecure
        self._x_auth = x_auth
        self._is_debug = is_debug
        self._is_http = is_http
        self._default_method = "https://"
        if self._is_http:
            self._default_method = "http://"

        self.logger = logging.getLogger(__name__)

        self.content_type = {
            'Content-Type': 'application/json; charset=utf-8'
        }
        self.json_content_type = {
            'Content-Type': 'application/json; charset=utf-8'
        }

        self._manage_servers_obs = []
        self._manage_chassis_obs = []
        # mainly to track query sent , for unit test
        self.query_counter = 0
        # run time
        self.action_targets = None
        self.api_endpoints = None

    @property
    def redfish_ip(self) -> str:
        """Redfish host, with the port appended when it is not the default 443.

        :return: the IP or hostname, suffixed with ``:port`` for non-443 ports.
        """
        if ":" in self._redfish_ip:
            return self._redfish_ip
        else:
            if self._port != 443:
                return f"{self._redfish_ip}:{self._port}"
            else:
                return self._redfish_ip

    @property
    def username(self) -> str:
        """Redfish account username.

        :return: the configured username.
        """
        return self._username

    @property
    def password(self) -> str:
        """Redfish account password.

        :return: the configured password.
        """
        return self._password

    @property
    def x_auth(self) -> str:
        """X-Auth token used in place of basic authentication.

        :return: the X-Auth token, or None when basic auth is used.
        """
        return self._x_auth

    def authentication_header(self):
        """Build the authentication header (placeholder; no-op in the base class)."""
        pass

    @staticmethod
    def redfish_error_handlers(status_code):
        """Raise the matching Redfish exception for a non-success HTTP status.

        :param status_code: the HTTP status code returned by the BMC.
        :raise AuthenticationFailed: on 401.
        :raise RedfishForbidden: on 403.
        :raise RedfishMethodNotAllowed: on 405.
        :raise RedfishNotAcceptable: on 406 or 409.
        """
        if status_code == 401:
            raise AuthenticationFailed(
                "Authentication failed."
            )
        if status_code == 403:
            raise RedfishForbidden(
                "Authentication failed."
            )
        if status_code == 403:
            raise RedfishForbidden(
                "Authentication failed."
            )
        if status_code == 405:
            raise RedfishMethodNotAllowed(
                "DELETE, GET, HEAD, POST, PUT, "
                "or PATCH , is not supported."
            )
        if status_code == 406:
            raise RedfishNotAcceptable(
                "Server rejected error code 406."
            )
        if status_code == 409:
            raise RedfishNotAcceptable(
                "Creation or update request could not be completed "
                "because it would cause a conflict "
                "in the current state of the resources."
            )

    @staticmethod
    async def async_default_error_handler(
            response: requests.models.Response) -> bool:
        """Default error handler for base query and redfish error code based on spec.
        :param response:
        :return:
        """
        if response.status_code >= 200 or response.status_code < 300:
            return True
        RedfishManager.redfish_error_handlers(response.status_code)

    async def api_async_get_call(self, loop, req, hdr: Dict):
        """Make api request either with x-auth authentication header or base authentication
        to redfish endpoint.

        :param loop: asyncio event loop
        :param req: request
        :param hdr: http header dict that will append to HTTP/HTTPS request.
        :return: request.
        """
        headers = {}
        headers.update(self.content_type)
        if hdr is not None:
            headers.update(hdr)

        if self.x_auth is not None:
            return loop.run_in_executor(
                None, functools.partial(
                    requests.get, req,
                    verify=self._is_verify_cert,
                    headers=headers
                )
            )
        else:
            return loop.run_in_executor(
                None, functools.partial(
                    requests.get, req,
                    verify=self._is_verify_cert,
                    auth=(self._username, self._password)
                )
            )

    def _http_session(self) -> requests.Session:
        """Return a cached keep-alive Session so many GETs reuse ONE connection.

        Historically every GET opened a fresh TCP+TLS connection. A full
        discovery crawl of a single BMC is hundreds of requests, and fragile
        embedded BMC HTTPS servers (seen live on GB300 HGX baseboards) drop
        connections and then wedge (stop answering on 443) under that handshake
        volume. A pooled ``requests.Session`` with HTTP keep-alive collapses the
        crawl onto a small reused connection pool, and a urllib3 ``Retry`` adds
        transport-level backoff for transient drops. Verify / auth / timeout
        semantics of the GET itself are unchanged. Tunable via env:
        ``REDFISH_HTTP_POOL`` (pool size), ``REDFISH_HTTP_RETRIES``,
        ``REDFISH_HTTP_BACKOFF`` (legacy ``IDRAC_HTTP_*`` still honored).
        Inherited by RedfishManagerBase.

        :return: the cached keep-alive ``requests.Session``.
        """
        session = getattr(self, "_session_cache", None)
        if session is None:
            session = requests.Session()
            pool = int(env_first("REDFISH_HTTP_POOL", "IDRAC_HTTP_POOL", default="4"))
            retries = Retry(
                total=int(env_first("REDFISH_HTTP_RETRIES", "IDRAC_HTTP_RETRIES", default="3")),
                backoff_factor=float(
                    env_first("REDFISH_HTTP_BACKOFF", "IDRAC_HTTP_BACKOFF", default="0.5")),
                status_forcelist=(500, 502, 503, 504),
                allowed_methods=frozenset(["GET"]),
                raise_on_status=False,
            )
            adapter = HTTPAdapter(
                pool_connections=pool, pool_maxsize=pool, max_retries=retries
            )
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            self._session_cache = session
        return session

    def api_get_call(
            self, req: str, hdr: Dict) -> requests.models.Response:
        """Make api request either with x-auth authentication
        header or base authentication to redfish.
        :param req:  request
        :param hdr: http header dict that will append to HTTP/HTTPS request.
        :return: request.
        """
        headers = {}
        headers.update(self.content_type)
        if hdr is not None:
            headers.update(hdr)

        # Bound every GET so a hung/unreachable BMC can't block forever.
        timeout = float(env_first("REDFISH_HTTP_TIMEOUT", "IDRAC_HTTP_TIMEOUT", default="30"))
        # Reuse one pooled keep-alive connection across GETs (see _http_session):
        # opening a fresh TLS connection per request wedges fragile BMCs.
        session = self._http_session()

        get_kwargs = {"verify": self._is_verify_cert, "timeout": timeout}
        if self._x_auth is not None:
            headers.update({'X-Auth-Token': self._x_auth})
        else:
            get_kwargs["auth"] = (self._username, self._password)

        # CLIENT span for the BMC call (no-op unless tracing is enabled). The BMC
        # renders as one inferred downstream service via peer.service in tracing.
        with tracing.client_span(req, "GET") as span:
            try:
                response = session.get(req, headers=headers, **get_kwargs)
            except Exception as exc:  # timeout / connection error → failed span
                tracing.record_exception(span, exc)
                raise
            tracing.record_response(span, response.status_code)
            return response

    def get_with_query(
            self, req: str,
            query: Optional[RedfishQuery] = None,
            hdr: Optional[Dict] = None,
            one_param_per_uri: bool = False) -> requests.models.Response:
        """GET ``req`` with optional Redfish query parameters applied.

        ``one_param_per_uri`` enforces the vendor rule (Dell iDRAC) that only one
        query parameter may appear per URI. The caller passes it from the target's
        vendor capability profile so this generic layer stays vendor-neutral.

        :param req: full request URL (without a query string)
        :param query: a RedfishQuery, or None for a plain GET
        :param hdr: optional headers
        :param one_param_per_uri: reject combining query parameters when True
        :return: requests.models.Response
        :raise ValueError: if the query is invalid for the target
        """
        url = req if query is None else query.apply(req, one_param_per_uri)
        return self.api_get_call(url, hdr or {})

    @staticmethod
    def expanded(level: Optional[int] = 1):
        """Return prefix to use for expanded respond.

         * Shall expand all hyperlinks, including those in

         * Number of levels the service should cascade the $expand operation.

         * . Shall expand all hyperlinks not in any links property instances of the resource,
             including those in payload annotations, such as @Redfish.Settings ,
             @Redfish.ActionInfo , and @Redfish.CollectionCapabilities .

         * ~ Shall expand all hyperlinks found in all links property instances of the resource.
        :param level:
        :return:
        """
        return f"?$expand=*($levels={level})"

    async def api_async_get_until_complete(self, req: str, hdr: Dict, loop=None):
        """Execute async get request
        :param req: api method caller request.
        :param hdr: dict: http/https header
        :param loop:  asyncio loop
        :return: http response object
        """
        if loop is None:
            loop = self._event_loop()
        response = await self.api_async_get_call(loop, req, hdr)
        await self.async_default_error_handler(await response)
        return await response

    @cached_property
    def _service_root(self):
        """The ``/redfish/v1/`` ServiceRoot document, fetched once per connection.

        Version, vendor, and the Systems path all live in this one document;
        a BMC round trip costs hundreds of milliseconds, so the identity
        properties below share a single fetch instead of each paying their own.

        :return: the ServiceRoot document as a dict, or None if the query failed.
        """
        api_resp = self.base_query("/redfish/v1/")
        return api_resp.data if api_resp is not None else None

    @cached_property
    def redfish_version(self) -> str:
        """Return version remote endpoint implemented
        :return:
        """
        data = self._service_root
        if data is not None and "RedfishVersion" in data:
            return data["RedfishVersion"]
        return ""

    @cached_property
    def redfish_vendor(self) -> str:
        """Return remote vendor
        :return:
        """
        data = self._service_root
        if data is not None and "Vendor" in data:
            return data["Vendor"]
        return ""

    @cached_property
    def redfish_system(self) -> str:
        """Return system path
        :return:
        """
        data = self._service_root
        if data is not None and "Systems" in data:
            return data["Systems"]["@odata.id"]
        return ""

    @staticmethod
    def select(select_property: Optional[str] = "") -> str:
        """Return a ``$select`` query-string fragment for the given property.

        :param select_property: the Redfish property name to select.
        :return: a query fragment of the form ``?$select=<property>``.
        """
        return f"?$select={select_property}"

    def base_query(self,
                   resource: str,
                   filename: Optional[str] = None,
                   do_async: Optional[bool] = False,
                   do_expanded: Optional[bool] = False,
                   select_target: Optional[str] = "",
                   query_expansion: Optional[str] = "",
                   data_type: Optional[str] = "json",
                   verbose: Optional[bool] = False,
                   key: Optional[str] = None,
                   **kwargs) -> CommandResult:
        """A base implementation for query redfish. This method shared
        by many other methods that require just a base http get query.

        do_expanded allow to leverage  $expand query parameter and
        enables a client to request a response that includes not only the
        requested resource, but also includes the contents of the
        subordinate or hyperlinked resource.

        Note tht expanded usually very chatty.

        By default,  base_query uses ?$expand=*($levels={level}

        :param select_target: select particular attribute
        :param resource: path to a redfish resource
        :param do_async: sync will subscribe to an event loop and issue async request.
        :param do_expanded:  will do expand query based on spec.
        :param query_expansion:  allow to overwrite expansion, and it always appended to request.
        :param filename: if filename indicate call will save the response to this file.
        :param verbose: enables verbose output, mainly to debug if endpoint return something strange.
        :param data_type: json or xml
        :param key: Optional json key in case we want to get something from a root element only.
        :return: CommandResult
        :raise RedfishException
        """
        if verbose:
            self.logger.debug(
                f"base_query received args"
                f"data_type: {data_type} "
                f"resource: {resource} "
                f"do_expanded:{do_expanded} "
                f"do_async: {do_async} "
                f"filename: {filename}")
            self.logger.debug(f"the rest of args: {kwargs}")

        headers = {}
        if data_type == "json":
            headers.update(self.json_content_type)

        # for expanded
        if len(query_expansion) > 0:
            r = f"{self._default_method}{self.redfish_ip}{resource}{self.expanded()}"
        elif do_expanded:
            r = f"{self._default_method}{self.redfish_ip}{resource}{self.expanded()}"
        else:
            r = f"{self._default_method}{self.redfish_ip}{resource}"

        if len(select_target) > 0:
            r = f"{self._default_method}{self.redfish_ip}" \
                f"{resource}{self.select(select_property=select_target)}"

        request_query = kwargs.get("redfish_query", None)
        if request_query is None:
            request_query = getattr(self, "_redfish_query", None)
        one_param_per_uri = kwargs.get(
            "redfish_query_one_param_per_uri",
            getattr(self, "_redfish_query_one_param_per_uri", False),
        )
        if (
            request_query is not None
            and not request_query.is_empty()
            and "?" not in r
        ):
            r = request_query.apply(r, one_param_per_uri)

        logging.debug(f"Sending request to {r}")

        def select_payload(payload):
            """Apply the optional root key selector to a copied payload.

            :param payload: parsed response payload copied out of the cache.
            :return: selected root value when requested, else ``payload``.
            """
            if (
                isinstance(payload, dict)
                and key is not None
                and len(key) > 0
                and key in payload
            ):
                return payload[key]
            return payload

        def load_response() -> tuple[object, object]:
            """Load and parse one Redfish GET response.

            :return: tuple of parsed payload and the HTTP Allow header.
            """
            response = self.api_get_call(r, headers)
            self.query_counter += 1
            self.default_error_handler(response)
            allow = response.headers.get("Allow")
            payload = response.json()
            return payload, allow

        cache = kwargs.get("redfish_cache", None)
        if cache is None:
            cache = active_redfish_response_cache()
        if not do_async:
            if cache is not None and filename is None:
                data, allow_header = cache.get_or_load(
                    (r, data_type), load_response)
            else:
                data, allow_header = load_response()
            data = select_payload(data)
        else:
            loop = self._event_loop()
            response = loop.run_until_complete(
                self.api_async_get_until_complete(
                    r, headers
                )
            )
            allow_header = response.headers.get("Allow")
            data = response.json()
            data = select_payload(data)

        save_if_needed(filename, data)
        return CommandResult(data, None, allow_header, None)

    @staticmethod
    def _redfish_error_from_payload(status_code: int, payload) -> RedfishError:
        """Build a RedfishError from an error payload and HTTP status code.

        :param status_code: the HTTP status code of the error response.
        :param payload: the parsed error body; a dict is mined for ``code``,
            ``message`` and extended-info entries, otherwise it is stringified.
        :return: the populated RedfishError.
        """
        if not isinstance(payload, dict):
            message = "" if payload is None else str(payload)
            return RedfishError(status_code, message=message)

        code = payload.get("code", "")
        message = payload.get("message", payload.get(RedfishJsonMessage.Message, ""))
        redfish_error = RedfishError(
            status_code,
            code=str(code or ""),
            message=str(message or ""),
        )

        message_extended = payload.get(RedfishJsonMessage.MessageExtendedInfo)
        if isinstance(message_extended, list):
            redfish_error.message_extended = [
                m for m in message_extended if isinstance(m, dict)
            ]

        return redfish_error

    @staticmethod
    def parse_error(error_response: requests.models.Response) -> RedfishError:
        """Default Parser for error msg from a JSON error.
        Note that respond can be same as success msg.

        :param error_response:
        :return:
        """
        redfish_error = RedfishError(error_response.status_code)

        try:
            err_resp = error_response.json()
            if not isinstance(err_resp, dict):
                return RedfishManager._redfish_error_from_payload(
                    error_response.status_code, err_resp
                )

            err_data = err_resp.get('error')
            if not isinstance(err_data, dict):
                if err_data is not None:
                    return RedfishManager._redfish_error_from_payload(
                        error_response.status_code, {"message": err_data}
                    )
                return RedfishManager._redfish_error_from_payload(
                    error_response.status_code, err_resp
                )

            redfish_error = RedfishManager._redfish_error_from_payload(
                error_response.status_code, err_data
            )
        except requests.exceptions.JSONDecodeError as json_err:
            redfish_error.exception_msg = str(json_err)
            return redfish_error

        return redfish_error

    @staticmethod
    def parse_json_respond_msg(
            resp: requests.models.Response) -> RedfishRespondMessage:
        """Default parser for json respond. For example if HTTP post or HTTP Delete
        return payload

        :param resp: requests.models.Response
        :return:
        """
        redfish_resp = RedfishRespondMessage(resp.status_code)
        try:
            json_data = resp.json()
            if RedfishJsonMessage.MessageExtendedInfo in json_data:
                redfish_resp.message_extended = [
                    m for m
                    in json_data[RedfishJsonMessage.MessageExtendedInfo]
                ]
        except requests.exceptions.JSONDecodeError as decode_err:
            logging.debug(f"no json body to parse from respond: {decode_err}")
        except TypeError as type_err:
            logging.debug(f"unexpected respond payload shape: {type_err}")

        return redfish_resp

    @staticmethod
    def default_error_handler(response) -> RedfishApiRespond:
        """Default error handler.
        :param response:
        :return:
        """
        if response.status_code == 200:
            return RedfishApiRespond.Ok
        if response.status_code == 202:
            return RedfishApiRespond.AcceptedTaskGenerated
        if response.status_code == 204:
            return RedfishApiRespond.Success
        if 200 <= response.status_code < 300:
            return RedfishApiRespond.Success
        if response.status_code == 401:
            raise RedfishUnauthorized("Unauthorized access")
        elif response.status_code == 403:
            raise RedfishForbidden("access forbidden")
        elif response.status_code == 404:
            error_msg = RedfishManager.parse_error(response)
            raise ResourceNotFound(error_msg)
        else:
            error_msg = RedfishManager.parse_error(response)
            raise ResourceNotFound(error_msg)

    @staticmethod
    def value_from_json_list(json_obj, k: str):
        """Try to parse the JSON object and get the key. It doesn't do a deep lookup.
        If an object is a list, it attempts to get a key. Note this specifically for cases
        When spec defines an array, but a list holds a single element.

        :param json_obj: could be a list , dict or string.
        :param k: a key
        :return: a value or None
        """
        # a case for list, return last
        if isinstance(json_obj, list) and len(json_obj) > 0:
            list_flat = json_obj[-1]
            if isinstance(list_flat, dict):
                if k in list_flat:
                    return list_flat[k]
        # a case for dict
        elif isinstance(json_obj, dict):
            if k in json_obj:
                return json_obj[k]
        # a case for str
        elif isinstance(json_obj, str):
            return json_obj
        else:
            return None

    @cached_property
    def members(self):
        """Redfish manager members.
        :return:
        """
        cmd_result = self.base_query(f"{RedfishApi.Managers}", key=RedfishJson.Members)
        return self.value_from_json_list(cmd_result.data, RedfishJson.Data_id)

    @abstractmethod
    def redfish_manage_servers(self) -> str:
        """Shared method return who remote endpoint managed servers
        and list as json ManagerForServers
        :return: return manager
        """
        api_resp = self.base_query(self.members, key=RedfishJson.Links)
        if api_resp.data is not None and RedfishJson.ManagerServers in api_resp.data:
            if isinstance(api_resp.data, dict):
                manage_servers = api_resp.data[RedfishJson.ManagerServers]
                self._manage_servers_obs = manage_servers
                return self.value_from_json_list(
                    manage_servers, RedfishJson.Data_id
                )
        else:
            self.logger.error("")
        return ""

    @staticmethod
    def job_id_from_header(
            response: requests.models.Response,
            strict: Optional[bool] = True) -> str:
        """Returns job id from the response header.
        :param strict: if true will raise exception.
        :param response: a response that should have job id information in the header.
        :return: job id from the Location header
        :raise TaskIdUnavailable if header not present.
        """
        job_id = ""
        resp_hdr = response.headers
        if RedfishJsonSpec.Location not in resp_hdr:
            if strict:
                raise TaskIdUnavailable(
                    "There is no location in the response header. "
                    "(not all api create job id)"
                )
        else:
            location = response.headers[RedfishJsonSpec.Location]
            job_id = location.split("/")[-1]

        return job_id

    @staticmethod
    def job_id_from_respond(
            response: requests.models.Response) -> str:
        """Try to parse job id from HTTP respond, otherwise empty string
        :param response: requests.models.Response
        :return: str: a job id or empty string
        """
        try:
            if response is not None and hasattr(response, "__dict__"):
                response_dict = str(response.__dict__)
                if response_dict is not None and len(response_dict) > 0:
                    job_id = re.search("JID_.+?,", response_dict)
                    if job_id is not None:
                        job_id = job_id.group(0)
                    return job_id
        except AttributeError as attr_err:
            logging.debug(f"could not read job id from respond object: {attr_err}")

        return ""

    def parse_task_id(self, data) -> str:
        """Parses input data and try to get a
        job id from the http header or http response.

        :param data:  http response or CommandResult
        :return: job_id or empty string.
        """
        # get response from extra
        if data is None:
            return ""

        # TODO this case I need remove
        if hasattr(data, "extra"):
            resp = data.extra
        elif isinstance(data, requests.models.Response):
            resp = data
        else:
            raise ValueError("Unknown data type.")

        if resp is None:
            return ""

        # this based on spec
        try:
            job_id = self.job_id_from_header(resp)
            logging.debug(f"idrac api returned job_id: {job_id} in the response header.")
            return job_id
        # optional lookup, fall through to the response body below.
        except TaskIdUnavailable as header_err:
            logging.debug(f"no job id in the response header: {header_err}")

        # this from response
        try:
            # try to get from the response, it an optional check.
            job_id = self.job_id_from_respond(resp)
            logging.debug(f"idrac api returned job_id: {job_id} in the response header.")
        except TaskIdUnavailable as respond_err:
            logging.debug(f"no job id in the response body: {respond_err}")

        return ""

    @abstractmethod
    def api_success_msg(self,
                        api_respond: RedfishApiRespond,
                        message_key: Optional[str] = "message",
                        message=None) -> Dict:
        """A default api success respond,
        Return dict contains Status, and it describes whether rest return
        ok, accepted or success.

        if message and msg key provide msg key added to a dict.
        for example if we want to add extra information about success.

        :param api_respond: respond enum. we report to upper ok, accepted, success.
        :param message_key: key we need add extra
        :param message: message information data
        :return: a dict
        """
        pass
