"""Redfish implementation based
on redfish specification.

https://www.dmtf.org/standards/REDFISH

Author Mus spyroot@gmail.com
"""

import argparse
import asyncio
import collections
import contextvars
import copy
import functools
import json
import logging
import re
import threading
import time
import uuid
from abc import abstractmethod
from collections.abc import Mapping
from contextlib import contextmanager
from functools import cached_property
from typing import Any, Callable, Dict, Hashable, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .cmd_exceptions import (
    AuthenticationFailed,
    ResourceNotFound,
    TaskIdUnavailable,
    UnsupportedAction,
)
from .cmd_utils import save_if_needed
from .config import http_backoff, http_pool, http_retries, http_timeout
from .custom_argparser.customer_argdefault import CustomArgumentDefaultsHelpFormatter
from .redfish_exceptions import (
    RedfishForbidden,
    RedfishMethodNotAllowed,
    RedfishNotAcceptable,
    RedfishUnauthorized,
)
from .redfish_query import RedfishQuery
from .redfish_respond import RedfishRespondMessage
from .redfish_respond_error import RedfishError
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .redfish_api_common import RedfishAction

from .redfish_api_common import HTTPMethod, RedfishAction
from .redfish_shared import (
    RedfishApi,
    RedfishApiRespond,
    RedfishJson,
    RedfishJsonMessage,
    RedfishJsonSpec,
)
from .redfish_task_state import TERMINAL_TASK_STATES, TaskState, TaskStatus
from .telemetry import tracing
from .telemetry.identity import service_instance_id_from_sources

module_logger = logging.getLogger(__name__)

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
                 host: Optional[str] = "",
                 username: Optional[str] = "root",
                 password: Optional[str] = "",
                 port: Optional[int] = 443,
                 insecure: Optional[bool] = True,
                 is_http: Optional[bool] = False,
                 x_auth: Optional[str] = None,
                 is_debug: Optional[bool] = False):
        """Default constructor for Redfish Manager.
           it requires a credentials to interact with redfish endpoint.
           By default, Redfish Manager uses json to serialize a data to callee
           and uses json content type.

        host, username, password, port, insecure and is_http are the shared
        common properties every BMC has; they live here ONCE and every vendor
        manager (IDrac/Ilo/Supermicro) inherits them — no per-vendor copy.

        :param host: BMC IP or hostname.
        :param username: BMC account username; defaults to root.
        :param password: BMC account password.
        :param port: BMC TCP port (default 443); accepts an int or str.
        :param insecure: when True (the default) TLS certificate verification is
            skipped. BMCs ship self-signed certificates, so verification is
            opt-in: pass ``insecure=False`` to verify the server certificate.
        :param is_http: use plain HTTP instead of HTTPS for requests when True.
        :param x_auth: X-Authentication header.
        :param is_debug: when True, include exception tracebacks in error logs.
        """
        self._redfish_ip = host
        self._username = username
        self._password = password

        if isinstance(port, str):
            port = int(port)

        self._port = port
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

    _registry = collections.defaultdict(dict)

    def __init_subclass__(cls, scm_type=None, name=None, **kwargs):
        """Initialize and register all sub-commands.
        :param scm_type:
        :param name: sub-command name to differentiate each subcommand
        :param kwargs:
        :return:
        """
        super().__init_subclass__(**kwargs)
        if scm_type is not None:
            cls._registry[scm_type][name] = cls

    @abstractmethod
    def execute(self, **kwargs) -> CommandResult:
        """Each sub-command must implement this method.  A dispatch automatically will
        dispatch to each command, each command discovered during initial phase."""
        pass

    @staticmethod
    @abstractmethod
    def register_subcommand(cls) -> Tuple[argparse.ArgumentParser, str, str]:
        """Each sub-command registers itself. Each command has its
        own set of arguments and optional arguments.
        :return: a Tuple that hold ArgumentParser, command name str, command help str
        """
        pass

    @classmethod
    def get_registry(cls):
        """Return the command registry visible to ``cls``: ``SET(DMTF) + SET(vendor)``.

        Walks the MRO base-first and merges each class's own ``_registry``, so a
        vendor manager's own commands override same-named DMTF commands and any
        verb the vendor never implemented falls back to the base -- the same
        inheritance the dispatcher (:meth:`_resolve_command`) applies at call
        time. A vendor manager that shadows ``_registry`` (declares its own dict)
        keeps its commands isolated from the base; this merge is the single place
        that recombines them, so no vendor needs its own override. For the neutral
        base it returns just ``SET(DMTF)``.

        :return: a dict mapping the ApiRequestType key to ``{name: command class}``.
        """
        merged = collections.defaultdict(dict)
        for klass in reversed(cls.__mro__):
            registry = klass.__dict__.get("_registry")
            if registry:
                for scm_type, bucket in registry.items():
                    merged[scm_type].update(bucket)
        return dict(merged)

    @classmethod
    def _resolve_command(cls, api_call, name):
        """Resolve a registered command across the MRO command registries.

        A vendor manager (``SupermicroManager``/``IloManager``) shadows its own
        command registry, so its own commands take precedence; but it must also
        reach the shared DMTF commands registered on ``RedfishManager``. Walking
        the MRO makes a vendor override win while the neutral base stays the
        fallback — the same way an inherited method routes to its parent class. A
        DMTF command registered on ``RedfishManager`` is therefore invokable
        through every vendor manager, not only ``IDracManager``.

        :param api_call: the ApiRequestType key of the command.
        :param name: the sub-command name.
        :return: the registered command class.
        :raises UnsupportedAction: when no registry in the MRO carries the command.
        """
        for klass in cls.__mro__:
            registry = klass.__dict__.get("_registry")
            if registry is None:
                continue
            bucket = registry.get(api_call)
            if bucket and name in bucket:
                return bucket[name]
        raise UnsupportedAction(f"Unknown {name} command.")

    @staticmethod
    def base_parser(is_async: Optional[bool] = True,
                    is_file_save: Optional[bool] = True,
                    is_expanded: Optional[bool] = True,
                    is_remote_share: Optional[bool] = False,
                    is_reboot: Optional[bool] = False):
        """Build the base optional parser shared by every subcommand.

        Each subcommand extends the returned parser with its own flags. This is
        vendor-neutral argparse construction, so it lives on the shared base and
        is inherited by every vendor manager.

        :param is_async: add the ``--async`` optional flag.
        :param is_file_save: add the ``--filename`` save-to-file option.
        :param is_expanded: add the ``--expanded`` expanded-query option.
        :param is_remote_share: add the CIFS/NFS/HTTP remote-share options.
        :param is_reboot: add the ``--reboot`` option (for cmds that reboot).
        :return: the base :class:`argparse.ArgumentParser`.
        """

        cmd_parser = argparse.ArgumentParser(
            add_help=False, formatter_class=CustomArgumentDefaultsHelpFormatter
        )

        output_parser = cmd_parser.add_argument_group('output', 'Output related options')
        chassis_parser = cmd_parser.add_argument_group('chassis', 'Chassis state options')

        if is_async:
            cmd_parser.add_argument(
                '-a', '--async', action='store_true',
                required=False, dest="do_async",
                default=False,
                help="will use async call."
            )

        if is_expanded:
            output_parser.add_argument(
                '-e', '--expanded', action='store_true',
                required=False, dest="do_expanded",
                default=False,
                help="expanded view, depend. it allows viewing more detail IDRAC data."
            )
        if is_file_save:
            output_parser.add_argument(
                '-f', '--filename', required=False, default="",
                type=str,
                help="filename, if we need to save a respond to a file."
            )

        if is_reboot:
            chassis_parser.add_argument(
                '-r', '--reboot', action='store_true',
                required=False, dest="do_reboot",
                default=False,
                help="will reboot a host.")

        # this optional args for remote share CIFS/NFS/HTTP etc.
        if is_remote_share:
            cmd_parser.add_argument(
                '--ip_addr', required=True,
                type=str, default=None,
                help="ip address for CIFS|NFS."
            )
            cmd_parser.add_argument(
                '--share_type', required=False,
                type=str, default="CIFS",
                help="share type CIFS|NFS."
            )
            cmd_parser.add_argument(
                '--share_name', required=True,
                type=str, default=None,
                help="share name."
            )
            cmd_parser.add_argument(
                '--remote_image', required=True,
                type=str, default=None,
                help="remote image. Example my_iso. "
            )
            cmd_parser.add_argument(
                '--remote_username', required=False,
                type=str, default="vmware",
                help="remote username if required."
            )
            cmd_parser.add_argument(
                '--remote_password', required=False,
                type=str, default="123456",
                help="password if required."
            )
            cmd_parser.add_argument(
                '--remote_workgroup', required=False,
                type=str, default="",
                help="group name if required."
            )
        return cmd_parser

    @staticmethod
    def _pop_connection_value(
            kwargs: dict, primary: str, legacy: str, internal: str):
        """Pop a dispatch connection argument, accepting deprecated aliases.

        :param kwargs: dispatch keyword arguments.
        :param primary: canonical keyword name.
        :param legacy: deprecated alias keyword name.
        :param internal: private keyword used by sync dispatch to avoid
            colliding with subcommand-local ``host`` or ``port`` arguments.
        :return: the popped value.
        :raises KeyError: when no connection key exists.
        """
        if internal in kwargs:
            value = kwargs.pop(internal)
            kwargs.pop(legacy, None)
            if kwargs.get(primary) in (value, None):
                kwargs.pop(primary, None)
            return value

        if primary in kwargs:
            value = kwargs.pop(primary)
            legacy_value = kwargs.pop(legacy, None)
            if value is not None:
                return value
            if legacy_value is not None:
                return legacy_value
            return value

        return kwargs.pop(legacy)

    @classmethod
    def invoke(cls,
               api_call: Hashable,
               name: str, **kwargs) -> CommandResult:
        """Main interface uses to invoke a command.

        :param api_call: api request type is enum for each cmd.
        :param name: a name is key for a given api request type.
                      So we can register under same type sub-commands.
        :param kwargs: command arguments plus connection arguments. Connection
            arguments accept canonical ``host``/``username``/``password``/``port``
            names, legacy ``idrac_*`` aliases, or private ``_redfish_*`` keys
            used by internal dispatch.
        :return: command result returned by the registered command.
        """
        disp = cls._resolve_command(api_call, name)
        _host = cls._pop_connection_value(
            kwargs, "host", "idrac_ip", "_redfish_host")
        _username = cls._pop_connection_value(
            kwargs, "username", "idrac_username", "_redfish_username")
        _password = cls._pop_connection_value(
            kwargs, "password", "idrac_password", "_redfish_password")
        _port = cls._pop_connection_value(
            kwargs, "port", "idrac_port", "_redfish_port")
        _insecure = kwargs.pop("insecure")
        _is_http = kwargs.pop("is_http")
        _redfish_query = kwargs.pop("redfish_query", None)
        _redfish_query_one_param_per_uri = kwargs.pop(
            "redfish_query_one_param_per_uri", False
        )
        _redfish_cache = kwargs.pop("redfish_cache", None)

        inst = disp(
            host=_host,
            username=_username,
            password=_password,
            port=_port,
            insecure=_insecure,
            is_http=_is_http
        )
        inst._redfish_query = _redfish_query
        inst._redfish_query_one_param_per_uri = _redfish_query_one_param_per_uri

        if _redfish_cache is None:
            return inst.execute(**kwargs)
        with redfish_response_cache_scope(_redfish_cache):
            return inst.execute(**kwargs)

    async def async_invoke(
            cls, api_call: Hashable, name: str, **kwargs) -> CommandResult:
        """Main interface uses to invoke a command.

        :param api_call: api request type is enum for each cmd.
        :param name: a name.
        :param kwargs: command arguments plus connection arguments. Connection
            arguments accept canonical ``host``/``username``/``password``/``port``
            names, legacy ``idrac_*`` aliases, or private ``_redfish_*`` keys
            used by internal dispatch.
        :return: CommandResult.
        """
        disp = cls._resolve_command(api_call, name)
        _host = cls._pop_connection_value(
            kwargs, "host", "idrac_ip", "_redfish_host")
        _username = cls._pop_connection_value(
            kwargs, "username", "idrac_username", "_redfish_username")
        _password = cls._pop_connection_value(
            kwargs, "password", "idrac_password", "_redfish_password")
        _port = cls._pop_connection_value(
            kwargs, "port", "idrac_port", "_redfish_port")
        _insecure = kwargs.pop("insecure")
        _is_http = kwargs.pop("is_http")
        _redfish_query = kwargs.pop("redfish_query", None)
        _redfish_query_one_param_per_uri = kwargs.pop(
            "redfish_query_one_param_per_uri", False
        )
        _redfish_cache = kwargs.pop("redfish_cache", None)
        module_logger.debug(f"dispatching {name} to Redfish port {_port}")

        inst = disp(
            host=_host,
            username=_username,
            password=_password,
            port=_port,
            insecure=_insecure,
            is_http=_is_http
        )
        inst._redfish_query = _redfish_query
        inst._redfish_query_one_param_per_uri = _redfish_query_one_param_per_uri
        # Operation root span named by the command (matches sync_invoke) so an
        # async command's BMC client spans nest into ONE trace instead of
        # surfacing as orphan "redfish.bmc.request" root traces in APM.
        with tracing.operation_span(name) as span:
            if _redfish_cache is None:
                result = inst.execute(**kwargs)
            else:
                with redfish_response_cache_scope(_redfish_cache):
                    result = inst.execute(**kwargs)
            tracing.record_result(span, result)
            return result

    def sync_invoke(self, api_call: Hashable, name: str, **kwargs) -> CommandResult:
        """Synchronous invocation of target command

        :param name: a name for command to differentiate sub-commands
        :param api_call: enum i.e. a type command that we need invoke
        :param kwargs: command-specific arguments. The manager injects private
            ``_redfish_*`` connection keys before dispatching to the registered
            command constructor.
        :return: Return result depends on actual command,
                 encapsulated in generic CommandResult
        """
        if len(self._username) == 0:
            raise ValueError("Username is empty string.")
        if len(self._password) == 0:
            raise ValueError("Password is empty string.")
        if len(self.redfish_ip) == 0:
            raise ValueError("Redfish host is empty string.")

        kwargs.update(
            {
                "_redfish_host": self.redfish_ip,
                "_redfish_username": self._username,
                "_redfish_password": self._password,
                "_redfish_port": self._port,
                # forward the original "skip verification" intent; _is_verify_cert
                # is the inverse (requests' verify flag), so flip it back here.
                "insecure": not self._is_verify_cert,
                "is_http": self._is_http,
            }
        )
        if "redfish_cache" not in kwargs:
            redfish_cache = active_redfish_response_cache()
            if redfish_cache is not None:
                kwargs["redfish_cache"] = redfish_cache
        # Operation root span named by the command (no-op unless tracing is on).
        # When main already opened the operation root, nest under it instead of
        # opening a second same-named root (the call stack IS the span tree); main
        # records the result on that root. Only root here for direct/standalone
        # callers (tests, nested tooling) where no operation span is active yet.
        if tracing.current_span() is not None:
            return self.invoke(api_call, name, **kwargs)
        with tracing.operation_span(name) as span:
            result = self.invoke(api_call, name, **kwargs)
            tracing.record_result(span, result)
            return result

    @property
    def redfish_ip(self) -> str:
        """Redfish host, with the port appended when it is not the default 443.

        :return: the IP or hostname, suffixed with ``:port`` for non-443 ports.
        """
        if ":" in self._redfish_ip:
            return self._redfish_ip
        else:
            # A None or default 443 port yields no suffix (an explicit :None
            # produced an unparseable URL); only a non-default port is appended.
            if self._port and self._port != 443:
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
        Inherited by IDracManager.

        :return: the cached keep-alive ``requests.Session``.
        """
        session = getattr(self, "_session_cache", None)
        if session is None:
            session = requests.Session()
            pool = http_pool()
            retries = Retry(
                total=http_retries(),
                backoff_factor=http_backoff(),
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
        timeout = http_timeout()
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

    # ------------------------------------------------------------------ #
    # Generic (DMTF) synchronous write path.
    #
    # Vendor-neutral counterpart to the Dell write flow in IDracManager. A
    # DMTF/Supermicro/HPE write is synchronous: it returns a success status
    # (200/204) with no Location task and no job id, so this path never fetches
    # a task. It still honours the specification's 202 + Location async form (id
    # pulled from the Location header via the shared job_id_from_header), which
    # Supermicro/HPE simply never send. Errors route through the shared
    # parse_error so the @Message.ExtendedInfo envelope surfaces identically to
    # Dell.
    #
    # IDracManager OVERRIDES every method below because its writes are built on
    # the Dell job/task system (status via _http_code_mapping, id in header OR
    # body, fetch_task over the Dell job model). MRO resolves a Dell instance to
    # those overrides and a non-Dell instance to these generic versions -- the
    # two must never cross-wire.
    # ------------------------------------------------------------------ #

    @staticmethod
    def _redact_sensitive_payload(payload):
        """Return a copy of a request payload with sensitive fields masked.

        Masks password-like top-level keys before a payload is written to a
        debug log so credentials never reach logs. IDracManager provides its own
        override; this generic version serves non-Dell instances.

        :param payload: the request payload mapping, or any value.
        :return: a redacted shallow copy when a mapping, else the value unchanged.
        """
        if not isinstance(payload, dict):
            return payload
        redacted = {}
        for key, value in payload.items():
            if isinstance(key, str) and "password" in key.lower():
                redacted[key] = "***"
            else:
                redacted[key] = value
        return redacted

    def _api_write_call(
            self, method: str, req: str, hdr: Dict,
            data: Optional[str] = None) -> requests.models.Response:
        """Shared transport for the synchronous write verbs (POST/PATCH/DELETE).

        Mirrors :meth:`api_get_call`: reuses the pooled keep-alive session,
        applies the bounded timeout, and authenticates with the X-Auth token
        when present, otherwise HTTP basic auth. Vendor-neutral; Dell provides
        its own api_*_call transport and never reaches this.

        :param method: the HTTP method name (POST/PATCH/DELETE).
        :param req: the fully qualified request URL.
        :param hdr: extra headers to merge onto the content-type headers.
        :param data: the serialized JSON body, or None for DELETE.
        :return: the raw :class:`requests.models.Response`.
        """
        headers = {}
        headers.update(self.content_type)
        if hdr is not None:
            headers.update(hdr)
        session = self._http_session()
        kwargs = {"verify": self._is_verify_cert, "timeout": http_timeout()}
        if data is not None:
            kwargs["data"] = data
        if self._x_auth is not None:
            headers.update({"X-Auth-Token": self._x_auth})
        else:
            kwargs["auth"] = (self._username, self._password)
        with tracing.client_span(req, method) as span:
            try:
                response = session.request(method, req, headers=headers, **kwargs)
            except Exception as exc:
                tracing.record_exception(span, exc)
                raise
            tracing.record_response(span, response.status_code)
            return response

    def api_post_call(self, req: str, payload: str, hdr: Dict) -> requests.models.Response:
        """Issue a synchronous HTTP POST to a Redfish resource.

        :param req: the fully qualified request URL.
        :param payload: the serialized JSON body.
        :param hdr: extra headers to merge onto the content-type headers.
        :return: the raw :class:`requests.models.Response`.
        """
        return self._api_write_call("POST", req, hdr, data=payload)

    def api_patch_call(self, req: str, payload: str, hdr: Dict) -> requests.models.Response:
        """Issue a synchronous HTTP PATCH to a Redfish resource.

        :param req: the fully qualified request URL.
        :param payload: the serialized JSON body.
        :param hdr: extra headers to merge onto the content-type headers.
        :return: the raw :class:`requests.models.Response`.
        """
        return self._api_write_call("PATCH", req, hdr, data=payload)

    def api_delete_call(self, req: str, hdr: Dict) -> requests.models.Response:
        """Issue a synchronous HTTP DELETE to a Redfish resource.

        :param req: the fully qualified request URL.
        :param hdr: extra headers to merge onto the content-type headers.
        :return: the raw :class:`requests.models.Response`.
        """
        return self._api_write_call("DELETE", req, hdr)

    def default_post_success(
            self, response: requests.models.Response,
            expected: Optional[int] = 202,
            ignore_error_code: Optional[int] = 0) -> RedfishApiRespond:
        """Map a write response to a RedfishApiRespond, raising on failure.

        Vendor-neutral status handling: an explicitly ignored status is treated
        as success, otherwise the shared :meth:`default_error_handler` classifies
        the code (2xx -> Ok/Success, 202 -> AcceptedTaskGenerated) and raises for
        4xx/5xx. Unlike the Dell override it consults no instance status table.

        :param response: the write HTTP response.
        :param expected: the status the caller treats as success (advisory here).
        :param ignore_error_code: an HTTP status to treat as success.
        :return: the mapped RedfishApiRespond.
        :raises RedfishUnauthorized: on HTTP 401.
        :raises RedfishForbidden: on HTTP 403.
        :raises ResourceNotFound: on HTTP 404 and other error statuses.
        """
        if ignore_error_code and response.status_code == ignore_error_code:
            return RedfishApiRespond.Success
        return self.default_error_handler(response)

    def default_patch_success(
            self, response: requests.models.Response,
            expected: Optional[int] = 202,
            ignore_error_code: Optional[int] = 0) -> RedfishApiRespond:
        """PATCH counterpart of :meth:`default_post_success`.

        :param response: the write HTTP response.
        :param expected: the status the caller treats as success (advisory here).
        :param ignore_error_code: an HTTP status to treat as success.
        :return: the mapped RedfishApiRespond.
        """
        return self.default_post_success(
            response, expected=expected, ignore_error_code=ignore_error_code)

    def default_delete_success(
            self, response: requests.models.Response,
            expected: Optional[int] = 202,
            ignore_error_code: Optional[int] = 0) -> RedfishApiRespond:
        """DELETE counterpart of :meth:`default_post_success`.

        :param response: the write HTTP response.
        :param expected: the status the caller treats as success (advisory here).
        :param ignore_error_code: an HTTP status to treat as success.
        :return: the mapped RedfishApiRespond.
        """
        return self.default_post_success(
            response, expected=expected, ignore_error_code=ignore_error_code)

    def base_request_respond(
            self, resource: str, method: HTTPMethod,
            payload: Optional[dict] = None,
            do_async: Optional[bool] = False,
            data_type: Optional[str] = "json",
            expected_status: Optional[int] = 200,
            ignore_error_code: Optional[int] = 0) -> tuple:
        """Vendor-neutral synchronous write orchestrator (POST/PATCH/DELETE).

        DMTF counterpart to ``IDracManager.base_request_respond``. A non-Dell
        write is synchronous: on success it returns a CommandResult with the
        success message and no task id. It still honours the specification's
        202 + Location async form (task id from the Location header via the
        shared job_id_from_header), which Supermicro/HPE never send. Errors
        propagate from the default_*_success handlers carrying the parsed
        RedfishError, so the operator sees the @Message.ExtendedInfo text.

        :param resource: the Redfish resource path (leading slash included).
        :param method: the :class:`HTTPMethod` to issue (POST/PATCH/DELETE).
        :param payload: the request body mapping, or None for an empty body.
        :param do_async: accepted for signature parity; this path is synchronous.
        :param data_type: the body content type; only ``"json"`` adds JSON headers.
        :param expected_status: the status the caller treats as success.
        :param ignore_error_code: an HTTP status to treat as success.
        :return: a tuple of (CommandResult, RedfishApiRespond).
        :raises UnsupportedAction: when ``method`` is not a write verb.
        """
        headers = {}
        if data_type == "json":
            headers.update(self.json_content_type)
        pd = payload if payload is not None else {}
        self.logger.debug(
            f"Issuing {method} request to resource: {resource}, "
            f"payload: {json.dumps(self._redact_sensitive_payload(pd))}"
        )
        r = f"{self._default_method}{self.redfish_ip}{resource}"
        if method == HTTPMethod.POST:
            response = self.api_post_call(r, json.dumps(pd), headers)
            api_resp = self.default_post_success(
                response, expected=expected_status, ignore_error_code=ignore_error_code)
        elif method == HTTPMethod.PATCH:
            response = self.api_patch_call(r, json.dumps(pd), headers)
            api_resp = self.default_patch_success(
                response, expected=expected_status, ignore_error_code=ignore_error_code)
        elif method == HTTPMethod.DELETE:
            response = self.api_delete_call(r, headers)
            api_resp = self.default_delete_success(
                response, expected=expected_status, ignore_error_code=ignore_error_code)
        else:
            raise UnsupportedAction(f"unsupported write method: {method}")

        if api_resp == RedfishApiRespond.AcceptedTaskGenerated:
            task_id = self.job_id_from_header(response, strict=False)
            return CommandResult({"task_id": task_id}, None, None, None), api_resp
        return CommandResult(self.api_success_msg(api_resp), None, None, None), api_resp

    def base_post(
            self, resource: str, payload: Optional[dict] = None,
            do_async: Optional[bool] = False, data_type: Optional[str] = "json",
            expected_status: Optional[int] = 204,
            ignore_error_code: Optional[int] = 0) -> tuple:
        """Vendor-neutral HTTP POST wrapper over :meth:`base_request_respond`.

        :param resource: the Redfish resource path.
        :param payload: the request body mapping, or None.
        :param do_async: accepted for parity; the base path is synchronous.
        :param data_type: the body content type.
        :param expected_status: the status the caller treats as success.
        :param ignore_error_code: an HTTP status to treat as success.
        :return: a tuple of (CommandResult, RedfishApiRespond).
        """
        return self.base_request_respond(
            resource, HTTPMethod.POST, payload=payload, do_async=do_async,
            data_type=data_type, expected_status=expected_status,
            ignore_error_code=ignore_error_code)

    def base_patch(
            self, resource: str, payload: Optional[dict] = None,
            do_async: Optional[bool] = False, data_type: Optional[str] = "json",
            expected_status: Optional[int] = 204,
            ignore_error_code: Optional[int] = 0) -> tuple:
        """Vendor-neutral HTTP PATCH wrapper over :meth:`base_request_respond`.

        :param resource: the Redfish resource path.
        :param payload: the request body mapping, or None.
        :param do_async: accepted for parity; the base path is synchronous.
        :param data_type: the body content type.
        :param expected_status: the status the caller treats as success.
        :param ignore_error_code: an HTTP status to treat as success.
        :return: a tuple of (CommandResult, RedfishApiRespond).
        """
        return self.base_request_respond(
            resource, HTTPMethod.PATCH, payload=payload, do_async=do_async,
            data_type=data_type, expected_status=expected_status,
            ignore_error_code=ignore_error_code)

    def base_delete(
            self, resource: str, payload: Optional[dict] = None,
            do_async: Optional[bool] = False, data_type: Optional[str] = "json",
            expected_status: Optional[int] = 204,
            ignore_error_code: Optional[int] = 0) -> tuple:
        """Vendor-neutral HTTP DELETE wrapper over :meth:`base_request_respond`.

        :param resource: the Redfish resource path.
        :param payload: the request body mapping, or None.
        :param do_async: accepted for parity; the base path is synchronous.
        :param data_type: the body content type.
        :param expected_status: the status the caller treats as success.
        :param ignore_error_code: an HTTP status to treat as success.
        :return: a tuple of (CommandResult, RedfishApiRespond).
        """
        return self.base_request_respond(
            resource, HTTPMethod.DELETE, payload=payload, do_async=do_async,
            data_type=data_type, expected_status=expected_status,
            ignore_error_code=ignore_error_code)

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

    def check_api_version(self):
        """Probe the Redfish service root for the API version and action targets.

        Issues a single GET to the service root (:data:`RedfishApi.Version`,
        ``/redfish/v1``) and records the advertised document on
        ``self.api_endpoints`` plus any service-root action targets on
        ``self.action_targets``. This is the vendor-neutral probe every manager
        inherits; a vendor that exposes an OEM service catalog (for example Dell's
        Lifecycle Controller service) overrides it to probe that first and fall
        back here. It must stay free of any OEM/Dell endpoint so non-Dell managers
        (Supermicro, HPE, generic DMTF) can rely on it.

        :return: a tuple of (service-root document, list of action target URIs).
        """
        headers = {}
        headers.update(self.json_content_type)
        r = f"{self._default_method}{self.redfish_ip}{RedfishApi.Version}"
        response = self.api_get_call(r, headers)
        self.default_error_handler(response)
        data = response.json()
        self.api_endpoints = data
        if RedfishJson.Actions in self.api_endpoints:
            actions = self.api_endpoints[RedfishJson.Actions]
            self.action_targets = [actions[k]['target'] for k in actions.keys()]
        return self.api_endpoints, self.action_targets

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

    @staticmethod
    def _flatten_action_targets(resource):
        """Map every ``#Type.Action`` (top-level and Oem) to its target URL.

        Unlike the short-name discovery map, this does NOT collapse two actions
        that share a short name, so an exact full-type lookup is unambiguous.

        :param resource: the parsed Redfish resource whose ``Actions`` block is read.
        :return: a dict mapping each action full type to its target URL.
        """
        out = {}
        actions = (resource or {}).get("Actions") or {}
        if not isinstance(actions, dict):
            return out
        for key, val in actions.items():
            if key == "Oem" and isinstance(val, dict):
                for ok, ov in val.items():
                    if isinstance(ov, dict) and ov.get("target"):
                        out[ok] = ov["target"]
            elif isinstance(val, dict) and val.get("target"):
                out[key] = val["target"]
        oem = (resource or {}).get("Oem") or {}
        if isinstance(oem, dict):
            for vendor_ext in oem.values():
                vendor_actions = (
                    vendor_ext.get("Actions") if isinstance(vendor_ext, dict) else None
                )
                if not isinstance(vendor_actions, dict):
                    continue
                for key, val in vendor_actions.items():
                    if isinstance(val, dict) and val.get("target"):
                        out[key] = val["target"]
        return out

    @staticmethod
    def _validate_action_payload(full_action_type: str,
                                 action: Optional["RedfishAction"],
                                 payload: dict) -> list[dict]:
        """Validate action payload keys/enum values when action metadata is available.

        :param full_action_type: the fully-qualified action type used to look up
            parameter metadata from the CSDL when the action has no inline args.
        :param action: the discovered RedfishAction (its inline ``args`` win over
            CSDL metadata), or None.
        :param payload: the action payload whose keys and values are checked.
        :return: a list of error dicts (one per offending parameter); empty when
            no metadata is available or every value is allowed.
        """
        inline_args = getattr(action, "args", None) or {}
        if inline_args:
            strict_names = False
            parameters = {
                name: {"allowed": tuple(values or ())}
                for name, values in inline_args.items()
            }
        else:
            from .redfish_csdl import action_parameters_for
            strict_names = True
            parameters = {
                name: {"allowed": param.allowable_values}
                for name, param in action_parameters_for(full_action_type).items()
            }
        if not parameters:
            return []

        errors = []
        for name, value in payload.items():
            if name not in parameters:
                if strict_names:
                    errors.append({
                        "parameter": name,
                        "value": value,
                        "allowed": [],
                    })
                continue
            allowed = tuple(parameters[name].get("allowed") or ())
            if allowed:
                values = value if isinstance(value, list) else [value]
                invalid = [item for item in values if item not in allowed]
                if invalid:
                    errors.append({
                        "parameter": name,
                        "value": invalid[0] if len(invalid) == 1 else invalid,
                        "allowed": sorted(allowed),
                    })
        return errors

    @staticmethod
    def _get_actions(cls, json_data):
        """Parse json from the manager for all supported action
        and action method arg.
        :param cls:
        :param json_data:
        :return:
        """
        unfiltered_actions = {}
        full_redfish_names = {}

        if RedfishJson.Actions not in json_data:
            return unfiltered_actions, full_redfish_names

        redfish_actions = json_data[RedfishJson.Actions]
        for a in redfish_actions:
            _ca = redfish_actions[a]
            if a == "Oem" and isinstance(_ca, dict):
                for k in _ca.keys():
                    rest_api_action = k.split(".")
                    if len(rest_api_action) < 2:
                        continue
                    rest_api_action = rest_api_action[-1]
                    unfiltered_actions[rest_api_action] = _ca[k]
                    full_redfish_names[rest_api_action] = k
            else:
                rest_api_action = a.split(".")
                if len(rest_api_action) < 2:
                    continue
                rest_api_action = rest_api_action[-1]
                unfiltered_actions[rest_api_action] = _ca
                full_redfish_names[rest_api_action] = a

        return unfiltered_actions, full_redfish_names

    @staticmethod
    def discover_member_redfish_actions(cls, json_data):
        """
        :param cls:
        :param json_data:
        :return:
        """
        action_dict = {}
        if RedfishJson.Members not in json_data:
            if RedfishJson.Actions in json_data:
                return cls.discover_redfish_actions(cls, json_data)
            else:
                return action_dict

        member_data = json_data[RedfishJson.Members]
        for m in member_data:
            if isinstance(m, dict):
                if RedfishJson.Actions in m.keys():
                    action = cls.discover_redfish_actions(cls, m)
                    action_dict.update(action)

        return action_dict

    @staticmethod
    def discover_redfish_actions(cls, json_data):
        """Discovers all redfish action, args and args choices.
        :param cls:
        :param json_data:
        :return:
        """
        if isinstance(json_data, requests.models.Response):
            json_data = json_data.json()

        action_dict = {}
        unfiltered_actions, full_redfish_names = cls._get_actions(cls, json_data)
        for ra in unfiltered_actions.keys():
            if 'target' not in unfiltered_actions[ra]:
                continue
            action_tuple = unfiltered_actions[ra]
            if isinstance(action_tuple, Dict):
                arg_keys = action_tuple.keys()
                redfish_action = RedfishAction(action_name=ra,
                                               target=action_tuple['target'],
                                               full_redfish_name=full_redfish_names[ra])
                action_dict[ra] = redfish_action
                for k in arg_keys:
                    if '@Redfish.AllowableValues' in k:
                        arg_name = k.split('@')[0]
                        action_dict[ra].add_action_arg(arg_name, action_tuple[k])

        return action_dict

    def invoke_action(self,
                      resource_uri: str,
                      action_name: str,
                      payload: Optional[dict] = None,
                      full_action_type: Optional[str] = None,
                      do_async: Optional[bool] = False,
                      expected_status: Optional[int] = 202,
                      dry_run: Optional[bool] = False,
                      confirm: Optional[bool] = False,
                      confirm_irreversible: Optional[bool] = False) -> CommandResult:
        """Resolve and POST a Redfish action, with a fail-safe destructiveness guard.

        Vendor-neutral: the action target is DISCOVERED from the owning resource's
        own ``Actions`` block (never a hardcoded URL), so the same call works on
        Dell, Supermicro/OpenBMC, HPE, etc. The action is classified by
        :func:`actions.action_policy.classify`; the guard is enforced HERE, not in
        the CLI, so a destructive POST cannot fire without explicit intent even if
        a caller wires the flags wrong:

        - READ_ONLY / REVERSIBLE  -> executes.
        - DESTRUCTIVE             -> dry-run unless ``confirm`` is True.
        - IRREVERSIBLE            -> dry-run unless BOTH ``confirm`` and
                                     ``confirm_irreversible`` are True.
        - unmapped action         -> treated as DESTRUCTIVE (fail-safe).

        On a dry-run NOTHING is POSTed; the resolved target + payload + level are
        returned in ``CommandResult.data`` for inspection. The owning resource is
        still GET-read to resolve the target (a harmless read).

        :param resource_uri: the resource whose Actions block names the target,
            e.g. ``/redfish/v1/Systems/System_0``.
        :param action_name: short action name as keyed by discover_redfish_actions,
            e.g. ``Reset``, ``InsertMedia``, ``SubmitTestEvent``.
        :param payload: JSON body to POST (None -> {}).
        :param full_action_type: exact ``#Type.Action`` to disambiguate when two
            actions collapse to the same short name (e.g. Reset vs ResetToDefaults).
        :param do_async: use the asyncio HTTP path.
        :param expected_status: expected POST status (202 async job / 204 sync).
        :param dry_run: force a dry-run regardless of classification.
        :param confirm: authorize a DESTRUCTIVE action to actually POST.
        :param confirm_irreversible: extra token required for IRREVERSIBLE actions.
        :return: CommandResult; ``.data`` carries action/target/level and either
            ``dry_run``/``blocked`` metadata or the POST result.
        """
        from .actions.action_policy import Destructiveness, classify

        try:
            resource = self.base_query(resource_uri, do_async=do_async).data or {}
        except Exception as e:
            return CommandResult(None, None, None, f"failed to read {resource_uri}: {e}")

        actions = self.discover_redfish_actions(self, resource)
        full = None
        target = None
        # Prefer an exact "#Type.Action" match read straight from the raw Actions
        # block. This is collision-proof: two actions can share a short name (e.g.
        # #Manager.ResetToDefaults vs the Oem #NvidiaManager.ResetToDefaults), and
        # the short-name discovery map keeps only one of them.
        if full_action_type:
            full_targets = self._flatten_action_targets(resource)
            if full_action_type in full_targets:
                full = full_action_type
                target = full_targets[full_action_type]
        # Otherwise fall back to the discovered short-name map.
        if target is None:
            action = actions.get(action_name)
            if action is not None and getattr(action, "target", None):
                full = action.full_redfish_name or f"#{action_name}"
                target = action.target
        if target is None:
            available = sorted(set(list(actions.keys())
                                   + list(self._flatten_action_targets(resource).keys())))
            wanted = full_action_type or action_name
            return CommandResult(
                {"action": wanted, "available": available}, actions, None,
                f"action '{wanted}' not found on {resource_uri}")

        level = classify(full)
        body = payload or {}
        action = actions.get(action_name)
        validation_errors = self._validate_action_payload(full, action, body)
        if validation_errors:
            first = validation_errors[0]
            action_label = full.lstrip("#")
            if first["allowed"]:
                error = (
                    f"invalid value for {action_label} {first['parameter']}: "
                    f"{first['value']}; allowed: {', '.join(first['allowed'])}"
                )
            else:
                error = f"unknown parameter for {action_label}: {first['parameter']}"
            return CommandResult({
                "action": full,
                "target": target,
                "payload": body,
                "level": level.value,
                "validation_errors": validation_errors,
            }, actions, None, error)

        # Fail-safe gate: decide whether this POST is actually allowed to fire.
        blocked_reason = None
        effective_dry = bool(dry_run)
        if level == Destructiveness.IRREVERSIBLE and not (confirm and confirm_irreversible):
            effective_dry = True
            blocked_reason = "irreversible action requires --confirm and --i-understand-irreversible"
        elif level == Destructiveness.DESTRUCTIVE and not confirm:
            effective_dry = True
            blocked_reason = "destructive action requires --confirm"

        if effective_dry:
            return CommandResult({
                "dry_run": True,
                "action": full,
                "target": target,
                "payload": body,
                "level": level.value,
                "blocked": blocked_reason,
            }, actions, None, None)

        span_attributes = {
            "redfish.action.name": action_name,
            "redfish.action.type": full,
            "redfish.action.target": target,
            "redfish.action.level": level.value,
        }
        with tracing.client_span_attributes(span_attributes):
            result, api_resp = self.base_post(target, payload=body, do_async=do_async,
                                              expected_status=expected_status)
        data = result.data if isinstance(result.data, dict) else {"result": result.data}
        data.setdefault("action", full)
        data.setdefault("target", target)
        data.setdefault("level", level.value)

        error = result.error
        if api_resp == RedfishApiRespond.Error or error is not None:
            data["executed"] = False
            if error is None:
                status_name = getattr(api_resp, "name", str(api_resp))
                error = f"action {full} failed with {status_name}"
            return CommandResult(data, actions, None, error)

        data.setdefault("executed", True)
        return CommandResult(data, actions, None, None)

    @staticmethod
    def _member_ids(members) -> list:
        """Extract every ``@odata.id`` from a Redfish ``Members`` list.

        Tolerates a non-list / malformed payload (returns ``[]``) and skips
        members without a string id, so a partial response never raises.

        :param members: the ``Members`` list from a Redfish collection.
        :return: the list of ``@odata.id`` strings (empty on a malformed payload).
        """
        if not isinstance(members, list):
            return []
        return [m[RedfishJson.Data_id] for m in members
                if isinstance(m, dict) and isinstance(m.get(RedfishJson.Data_id), str)]

    def discover_computer_system_ids(self) -> list:
        """Return ALL ComputerSystem ids from ``/redfish/v1/Systems``.

        ``idrac_manage_servers`` resolves a single system via the manager's
        ``ManagerForServers`` link and (through ``value_from_json_list``) returns
        only the last member — wrong on multi-system hosts. This enumerates the
        Systems collection so callers can pick the right one: e.g. a Supermicro
        GB300 exposes ``/redfish/v1/Systems/System_0`` (host) and
        ``/redfish/v1/Systems/HGX_Baseboard_0`` (NVIDIA GPU baseboard).

        :return: the list of ComputerSystem ``@odata.id`` paths.
        """
        cmd_result = self.base_query(RedfishApi.Systems, key=RedfishJson.Members)
        return self._member_ids(cmd_result.data)

    def discover_manager_ids(self) -> list:
        """Return ALL Manager ids from ``/redfish/v1/Managers`` (e.g. BMC_0, HGX_BMC_0).

        Companion to :meth:`discover_computer_system_ids` for boxes with more
        than one BMC; ``idrac_members`` only yields a single (last) manager.

        :return: the list of Manager ``@odata.id`` paths.
        """
        cmd_result = self.base_query(RedfishApi.Managers, key=RedfishJson.Members)
        return self._member_ids(cmd_result.data)

    def _host_system(self, system_ids) -> str:
        """Return the host ComputerSystem id from a multi-system collection.

        The host exposes a ``Bios``/``Boot`` link (where boot/bios/storage live);
        on a split-topology box the others are baseboards (e.g. the NVIDIA HGX
        baseboard carries GPUs but no Bios). Returns "" if undecidable.

        :param system_ids: candidate ComputerSystem ids to probe.
        :return: the id exposing a Bios/Boot link, or "" if none qualifies.
        """
        for sid in system_ids:
            try:
                data = self.base_query(sid).data
            except Exception:
                continue
            if isinstance(data, dict) and ("Bios" in data or "Boot" in data):
                return sid
        return ""


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
        """Parse a Dell Lifecycle Controller job id (``JID_...``) from the body.

        Dell returns the job id primarily in the ``Location`` header, but some
        responses carry it in the JSON body as ``{"id": "JID_414099044945",
        ...}``. This reads the body's ``id``/``Id`` field first, then falls back
        to scanning the serialized body for a ``JID_`` token, and returns ``""``
        when the body carries none. It never raises: a body without a job id is a
        normal, expected outcome the caller treats as "no job created".

        :param response: the write HTTP response.
        :return: the ``JID_...`` job id, or ``""`` when the body carries none.
        """
        if response is None:
            return ""
        body = None
        try:
            body = response.json()
        except (ValueError, TypeError, AttributeError):
            body = None
        if isinstance(body, dict):
            for key in ("id", "Id", "JobID", "JID"):
                value = body.get(key)
                if isinstance(value, str) and value.startswith("JID_"):
                    return value
        try:
            text = response.text if hasattr(response, "text") else json.dumps(body)
        except (TypeError, ValueError):
            text = ""
        match = re.search(r"JID_[0-9A-Za-z]+", text or "")
        return match.group(0) if match else ""

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

    def get_task_state(
            self, resp: requests.models.Response
    ) -> Tuple[Optional[TaskState], Optional[TaskStatus]]:
        """Parse a DMTF ``#Task`` response into its state and status.

        Reads the generic ``TaskState``/``TaskStatus`` properties a Redfish
        ``TaskService`` serves on ``/redfish/v1/TaskService/Tasks/{id}``. Unlike
        the Dell ``IDracManager.get_task_state``, it never consults the
        ``/Oem/Dell/Jobs`` ``JobState`` and raises nothing on a missing key: an
        absent, non-JSON, or non-spec value maps to ``None`` so the caller keeps
        its last observed state.

        :param resp: a requests.models.Response holding a ``#Task`` body.
        :return: a ``(TaskState, TaskStatus)`` tuple; either element is ``None``
            when the body is not a JSON object, the key is absent, or the value
            is not a DMTF-defined enum member.
        """
        try:
            data = resp.json()
        except requests.exceptions.JSONDecodeError as json_err:
            self.logger.debug(f"task response carried no json body: {json_err}")
            return None, None
        if not isinstance(data, dict):
            return None, None

        def _coerce(enum_cls, value):
            """Return the enum member for a wire value, or None if not a member.

            :param enum_cls: the enum class to coerce into (TaskState / TaskStatus).
            :param value: the raw wire value read from the #Task body.
            :return: the matching enum member, or None when value is not a member.
            """
            try:
                return enum_cls(value)
            except ValueError:
                return None

        return (
            _coerce(TaskState, data.get(RedfishJson.TaskState)),
            _coerce(TaskStatus, data.get(RedfishJson.TaskStatus)),
        )

    def fetch_task(
            self,
            task_id: str,
            sleep_time: Optional[int] = 10,
            wait_for_state: Optional[TaskState] = None,
            timeout: Optional[float] = None,
    ) -> Optional[TaskState]:
        """Poll the generic DMTF ``TaskService`` until a task finishes.

        Blocks on ``GET /redfish/v1/TaskService/Tasks/{task_id}``, following the
        Redfish task-monitor semantics: ``202 Accepted`` while the task runs,
        ``200 OK`` once it carries a state, ``404``/``410`` when a cancelled task
        is reaped. Returns as soon as the task reaches a terminal state
        (Completed/Killed/Cancelled/Exception) or the optional ``wait_for_state``.
        This is the vendor-neutral counterpart to ``IDracManager.fetch_task``,
        which additionally consults the Dell OEM ``/Oem/Dell/Jobs`` job model;
        here only the specification's ``TaskService`` is polled.

        :param task_id: the ``Id`` of the task, as returned when it was created.
        :param sleep_time: seconds to wait between polls; a server ``Retry-After``
            header takes precedence when larger.
        :param wait_for_state: return as soon as this state is observed, instead
            of waiting for a terminal state (e.g. resume once ``Running``).
        :param timeout: optional wall-clock budget in seconds; ``None`` waits
            until a terminal state or the task is reaped.
        :return: the last observed :class:`TaskState`, or ``None`` if the task
            never reported a recognised state.
        :raise AuthenticationFailed: if the service returns HTTP 401.
        """
        url = f"{self._default_method}{self.redfish_ip}{RedfishApi.Tasks}{task_id}"
        started = time.monotonic()
        task_state: Optional[TaskState] = None
        poll_count = 0

        # One INTERNAL span for the whole poll; each api_get_call below nests as a
        # CLIENT child automatically because the OTel context is the call stack.
        with tracing.poll_task_span() as poll_span:
            try:
                while True:
                    resp = self.api_get_call(url, {})
                    poll_count += 1
                    code = resp.status_code

                    if code == 401:
                        raise AuthenticationFailed("task service returned 401.")
                    # A reaped/cancelled task monitor returns 410 Gone or 404 Not
                    # Found; a 5xx ends the wait. Keep the last state seen.
                    if code in (404, 410) or code >= 500:
                        self.logger.info(
                            f"task {task_id} monitor returned {code}; stopping poll."
                        )
                        break

                    state, _status = self.get_task_state(resp)
                    if state is not None:
                        task_state = state
                    if wait_for_state is not None and task_state == wait_for_state:
                        break
                    if task_state in TERMINAL_TASK_STATES:
                        break

                    try:
                        retry_after = int(resp.headers.get("Retry-After", 0) or 0)
                    except (TypeError, ValueError):
                        retry_after = 0
                    delay = max(int(sleep_time or 0), retry_after)

                    if timeout is not None and (time.monotonic() - started) >= timeout:
                        self.logger.info(
                            f"task {task_id} poll timed out after {timeout}s."
                        )
                        break
                    time.sleep(delay)
            finally:
                self._set_poll_span_attributes(
                    poll_span, poll_count, sleep_time, started, task_state
                )

        return task_state

    @staticmethod
    def _set_poll_span_attributes(span, poll_count, sleep_time, started, task_state):
        """Set the required ``poll_task_span`` attributes on the yielded poll span.

        Runs in a ``finally`` so the contract keys are present even when the poll
        exits via exception (e.g. a 401 mid-poll).

        :param span: the poll span, or None when tracing is off.
        :param poll_count: number of BMC polls performed.
        :param sleep_time: configured inter-poll sleep in seconds.
        :param started: monotonic start time of the poll.
        :param task_state: the last observed TaskState, or None.
        :return: None.
        """
        if span is None:
            return
        span.set_attribute("poll.count", poll_count)
        span.set_attribute("poll.interval_ms", int((sleep_time or 0) * 1000))
        span.set_attribute("poll.elapsed_ms", int((time.monotonic() - started) * 1000))
        span.set_attribute("poll.terminal_state", task_state in TERMINAL_TASK_STATES)
        span.set_attribute(
            "redfish.task.state",
            task_state.value if task_state is not None else "unknown",
        )

    def discover_virtual_media_uri(self, do_async: Optional[bool] = False) -> str:
        """Resolve the VirtualMedia collection URI, vendor-neutrally.

        Dell hangs VirtualMedia off the ComputerSystem; iLO and Supermicro hang it
        off a Manager. Check every Manager first, then the host System, returning
        the first that advertises a VirtualMedia link. Falls back to the Dell
        ``{system}/VirtualMedia`` subpath so existing Dell behavior is unchanged.

        :param do_async: issue the underlying queries asynchronously when True.
        :return: the VirtualMedia collection URI.
        :raise ResourceNotFound: if no VirtualMedia collection can be resolved.
        """
        # Managers first for iLO/Supermicro/OpenBMC; keep the historical Dell
        # System.Embedded.1 preference when both System and Manager advertise it.
        manager_roots = []
        try:
            manager_roots.extend(self.discover_manager_ids() or [])
        except Exception:
            pass
        try:
            host_system = self.idrac_manage_servers
        except Exception:
            host_system = ""
        system_roots = []
        if not host_system:
            try:
                system_roots.extend(self.discover_computer_system_ids() or [])
            except Exception:
                pass
        roots = []
        if host_system.endswith("/System.Embedded.1"):
            roots.append(host_system)
        roots.extend(root for root in manager_roots if root not in roots)
        if host_system and host_system not in roots:
            roots.append(host_system)
        roots.extend(root for root in system_roots if root not in roots)
        for root in roots:
            try:
                data = self.base_query(root, do_async=do_async).data or {}
            except Exception:
                continue
            link = data.get("VirtualMedia")
            uri = link.get("@odata.id") if isinstance(link, dict) else None
            if uri:
                return uri
        fallback_system = host_system or (system_roots[0] if system_roots else "")
        if fallback_system:
            return f"{fallback_system}/VirtualMedia"
        raise ResourceNotFound("VirtualMedia collection not found in Managers or Systems")

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

    @staticmethod
    def _members(data):
        """Return @odata.id strings from a Redfish collection.

        :param data: parsed Redfish collection resource, or any value.
        :return: list of member @odata.id strings; empty when data is not a collection.
        """
        if not isinstance(data, dict):
            return []
        return [m["@odata.id"] for m in data.get("Members", [])
                if isinstance(m, dict) and isinstance(m.get("@odata.id"), str)]

    def _identity_resource(self, uri: str, redfish_cache, do_async: bool) -> dict:
        """Read one identity-discovery resource, tolerating unsupported paths.

        :param uri: Redfish resource URI.
        :param redfish_cache: current scrape response cache.
        :param do_async: whether the caller selected asynchronous queries.
        :return: resource mapping or an empty mapping.
        :raises Exception: transport and non-404 failures from the Redfish query.
        """
        try:
            result = self.base_query(
                uri,
                do_async=do_async,
                redfish_cache=redfish_cache,
            )
        except ResourceNotFound:
            return {}
        return result.data if isinstance(result.data, dict) else {}

    @staticmethod
    def _identity_link(resource: Mapping, key: str) -> Optional[str]:
        """Return a Redfish link URI from an identity-discovery resource.

        :param resource: Redfish resource mapping.
        :param key: linked property name.
        :return: linked ``@odata.id`` or None.
        """
        link = resource.get(key)
        if not isinstance(link, Mapping):
            return None
        uri = link.get("@odata.id")
        return uri if isinstance(uri, str) and uri else None

    @staticmethod
    def _chassis_identity_rank(item: tuple[str, Mapping]) -> tuple[int, str]:
        """Rank BMC/DC-SCM chassis ahead of unrelated enclosure resources.

        :param item: pair of chassis URI and resource mapping.
        :return: stable sort key with the most relevant chassis first.
        """
        uri, resource = item
        description = " ".join(str(resource.get(key) or "")
                               for key in ("Id", "Name", "Model"))
        normalized = description.lower().replace("_", "-")
        if "bmc" in normalized or "dc-scm" in normalized or "dcscm" in normalized:
            return (0, uri)
        if str(resource.get("ChassisType") or "").lower() in {"module", "component"}:
            return (1, uri)
        return (2, uri)

    def _discover_service_instance_id(
            self,
            redfish_cache: RedfishResponseCache,
            do_async: bool = False) -> Optional[str]:
        """Derive a stable global service instance UUID from Redfish identity.

        Source precedence is Manager UUID, BMC/DC-SCM chassis serial, burned-in
        management MAC, configurable management MAC, then a random UUID fallback.

        :param redfish_cache: current scrape response cache.
        :param do_async: whether the caller selected asynchronous queries.
        :return: canonical service.instance.id UUID text, or None when no stable
            source is available.
        """
        managers_collection = self._identity_resource(
            RedfishApi.Managers, redfish_cache, do_async)
        
        manager_resources = []
        for uri in sorted(self._members(managers_collection)):
            manager_resources.append((
                uri,
                self._identity_resource(uri, redfish_cache, do_async),
            ))

        manager_uuids = [resource.get("UUID") for _, resource in manager_resources]
        instance_id = service_instance_id_from_sources(
            manager_uuids=manager_uuids,
        )
        if instance_id is not None:
            return instance_id

        chassis_collection = self._identity_resource(
            RedfishApi.Chassis, redfish_cache, do_async)
        chassis_resources = []
        for uri in sorted(self._members(chassis_collection)):
            chassis_resources.append((
                uri,
                self._identity_resource(uri, redfish_cache, do_async),
            ))
        chassis_resources.sort(key=self._chassis_identity_rank)
        chassis_serials = [resource.get("SerialNumber")
                           for _, resource in chassis_resources]

        instance_id = service_instance_id_from_sources(
            chassis_serials=chassis_serials,
        )
        if instance_id is not None:
            return instance_id

        mac_addresses = []
        for _, manager in manager_resources:
            collection_uri = self._identity_link(manager, "EthernetInterfaces")
            if not collection_uri:
                continue
            collection = self._identity_resource(
                collection_uri, redfish_cache, do_async)
            for interface_uri in sorted(self._members(collection)):
                interface = self._identity_resource(
                    interface_uri, redfish_cache, do_async)
                instance_id = service_instance_id_from_sources(
                    permanent_macs=[interface.get("PermanentMACAddress")],
                )
                if instance_id is not None:
                    return instance_id
                mac_addresses.append(interface.get("MACAddress"))

        return service_instance_id_from_sources(
            mac_addresses=mac_addresses,
        )

    def _default_service_instance_id(
            self,
            redfish_cache: RedfishResponseCache,
            do_async: bool = False) -> str:
        """Return a stable discovered ID or a retryable process fallback.

        A discovered BMC identity is cached permanently. A random fallback is
        also process-stable, but discovery is retried on later scrapes so a
        transient read failure cannot pin the fallback for the process lifetime.

        :param redfish_cache: current scrape response cache.
        :param do_async: whether the caller selected asynchronous queries.
        :return: canonical service.instance.id UUID text.
        """
        discovered = getattr(self, "_derived_service_instance_id", None)
        if discovered is not None:
            return discovered
        try:
            discovered = self._discover_service_instance_id(
                redfish_cache,
                do_async=do_async,
            )
        except Exception as exc:  # an unreachable/malformed BMC must not crash the scrape
            self.logger.debug("service.instance.id discovery failed: %s", exc)
            discovered = None
        if discovered is not None:
            self._derived_service_instance_id = discovered
            return discovered
        fallback = getattr(self, "_fallback_service_instance_id", None)
        if fallback is None:
            fallback = str(uuid.uuid4())
            self._fallback_service_instance_id = fallback
            self.logger.warning(
                "No stable Redfish service instance identity was available; "
                "using a random UUID while discovery is retried")
        return fallback
