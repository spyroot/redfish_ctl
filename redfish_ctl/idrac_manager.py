"""IDracManager

redfish_ctl interacts with any Redfish-capable BMC (Dell iDRAC, Supermicro,
HPE iLO, and generic DMTF Redfish) via the REST API interface.

Main class command line tools utilizes. Each command must inherit
from this class. The class itself provides a register pattern where
each sub-command is registered automatically.
During the module phase, each sub-command is discovered and loaded,
allowing anyone to extend and add their own set of subcommands easily.

- The interaction with the BMC is done via the REST API.
- Each command must provide option invoke command synchronously
  or asynchronously

Each command return CommandResult named tuple where data
is actually data returned from rest API response.

CommandResult.discovered hold all rest endpoint.

https://www.dell.com/support/manuals/en-us/idrac9-lifecycle-controller-v4.x-series/idrac9_4.00.00.00_redfishapiguide_pub/redfish-resources?guid=guid-d3e85da8-5d22-4eb1-82ff-d2fdd4cd7730&lang=en-us

Author Mus spyroot@gmail.com
"""
import argparse
import functools
import json
import logging
from abc import abstractmethod
from datetime import datetime
from functools import cached_property
from typing import Dict, Optional, Tuple

import requests

from .cmd_exceptions import (
    DeleteRequestFailed,
    InvalidArgumentFormat,
    MissingMandatoryArguments,
    PatchRequestFailed,
    PostRequestFailed,
    ResourceNotFound,
    UnexpectedResponse,
)
from .custom_argparser.customer_argdefault import CustomArgumentDefaultsHelpFormatter
from .decorators.fault_injection import (
    simulate_http_faults,
    simulate_http_faults_async,
)
from .idrac_shared import (
    REDFISH_API,
    REDFISH_JSON,
    ApiRequestType,
    ApiRespondString,
    CliJobTypes,
    HTTPMethod,
    IDRACJobType,
    JobApplyTypes,
    PowerState,
    RedfishAction,
    RedfishApiRespond,
    ScheduleJobType,
)
from .idrac_shared import ResetType as ResetType
from .redfish_exceptions import RedfishException, RedfishForbidden, RedfishUnauthorized
from .redfish_manager import (
    CommandResult,
    RedfishManager,
)
from .redfish_shared import RedfishApi, RedfishJson, RedfishJsonSpec, env_first
from .telemetry import tracing

module_logger = logging.getLogger('redfish_ctl.idrac_manager')


def _simulated_connection_error() -> BaseException:
    """Build the exception a dropped BMC connection raises (SIMULATE_NETWORK_FAILURE).

    :return: a requests ConnectionError mirroring a real transport-level failure, so the
        downstream error handling and recorded span match a genuine network drop.
    """
    return requests.exceptions.ConnectionError(
        "simulated network failure (SIMULATE_NETWORK_FAILURE)"
    )


def _simulated_read_timeout() -> BaseException:
    """Build the exception a stalled BMC read raises (SIMULATE_NETWORK_TIMEOUT).

    :return: a requests ReadTimeout mirroring a real read-timeout failure, so the
        downstream error handling and recorded span match a genuine stalled read.
    """
    return requests.exceptions.ReadTimeout(
        "simulated network timeout (SIMULATE_NETWORK_TIMEOUT)"
    )


class IDracManager(RedfishManager):
    """
    IDracManager Class, interact with a Redfish endpoint via REST API interface
    """

    #: A connection nothing has classified yet presumes Dell — exactly the
    #: pre-profile behavior of the Dell-based command family. Evidence (a
    #: cached ServiceRoot classification) always outranks this presumption.
    _default_profile_vendor = "dell"

    def __init__(self,
                 host: Optional[str] = "",
                 username: Optional[str] = "root",
                 password: Optional[str] = "",
                 port: Optional[int] = 443,
                 insecure: Optional[bool] = True,
                 x_auth: Optional[str] = None,
                 is_http: Optional[bool] = False,
                 is_debug: Optional[bool] = False,
                 log_level=logging.NOTSET,
                 **kwargs):
        """Default constructor requires credentials.
           By default, the manager uses json to serialize a data to callee
           and uses json content type.

        One credential parameter set exists: ``host``/``username``/``password``/
        ``port``. The legacy ``idrac_*`` spellings are no longer parameters;
        they arrive via ``**kwargs`` and coalesce in the base constructor for
        one wave, after which they live only at the CLI/env edge (config.py,
        argparse).

        :param host: BMC host or IP address.
        :param username: BMC account username; defaults to root.
        :param password: BMC account password.
        :param port: BMC TCP port (default 443); accepts an int or str.
        :param insecure: when True (the default) TLS certificate verification is
            skipped. BMC controllers present self-signed certificates, so
            verification is opt-in: pass ``insecure=False`` to verify the cert.
        :param x_auth: X-Authentication header.
        :param is_http: use plain HTTP instead of HTTPS for requests when True.
        :param is_debug: when True, include exception tracebacks in error logs.
        :param log_level: logging level applied to this manager's logger.
        :param kwargs: forwarded to the base constructor (legacy connection
            aliases coalesce there).
        """
        super().__init__(host=host,
                         username=username,
                         password=password,
                         port=port,
                         insecure=insecure,
                         is_http=is_http,
                         x_auth=x_auth,
                         is_debug=is_debug,
                         **kwargs)

        self.logger = logging.getLogger(__name__)
        self._logger_level = log_level
        self.logger.setLevel(self._logger_level)

        # mapping between rest API respond to respected
        # string that we report to apper layer.
        self._api_respond_to_string = {
            RedfishApiRespond.Ok: ApiRespondString.Ok,
            RedfishApiRespond.Error: ApiRespondString.Error,
            RedfishApiRespond.Success: ApiRespondString.Success,
            RedfishApiRespond.AcceptedTaskGenerated: ApiRespondString.AcceptedTaskGenerated,

        }

        # mapping from cli to job types
        self._cli_job_type_mapping = {
            CliJobTypes.Bios_Config.value: IDRACJobType.BIOSConfiguration.value,
            CliJobTypes.OsDeploy.value: IDRACJobType.OSDeploy.value,
            CliJobTypes.FirmwareUpdate.value: IDRACJobType.FirmwareUpdate.value,
            CliJobTypes.RebootNoForce.value: IDRACJobType.RebootNoForce.value
        }

        self._redfish_error = None

    @property
    def _http_code_mapping(self):
        """Transitional view of the Dell status rows, now owned by DellProfile.

        The map (200/201/202/204 rows, including the Dell-only 201=Created)
        moved to ``DellProfile._status_map`` — the single source. Non-chokepoint
        consumers (``read_api_respond``, the subscription lifecycle helper)
        still read ``self._http_code_mapping``; this property serves them until
        those paths route through the profile. Read-only by contract.

        :return: the Dell status-code map.
        """
        from .dell_profile import DellProfile
        return DellProfile._status_map

    @property
    def idrac_ip(self) -> str:
        """BMC host address (delegates to :attr:`redfish_ip`).

        :return: the IP or hostname, suffixed with ``:port`` for non-443 ports.
        """
        return self.redfish_ip

    @property
    def host(self) -> str:
        """BMC host address (canonical alias for :attr:`redfish_ip`).

        :return: the IP or hostname, suffixed with ``:port`` for non-443 ports.
        """
        return self.redfish_ip

    @simulate_http_faults_async(_simulated_connection_error, _simulated_read_timeout)
    async def api_async_get_call(self, loop, req, hdr: Dict):
        """Make api asynced requests either with x-auth authentication
         header or base authentication.
        If event loop is none it will create one.
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

    @simulate_http_faults(_simulated_connection_error, _simulated_read_timeout)
    def api_get_call(
            self, req: str, hdr: Dict) -> requests.models.Response:
        """Make api request either with x-auth authentication header or redfish_ctl.
        :param req:  request
        :param hdr: http header dict that will append to HTTP/HTTPS request.
        :return: request.
        """
        headers = {}
        headers.update(self.content_type)
        if hdr is not None:
            headers.update(hdr)

        # Bound every GET so a hung/unreachable BMC can't block a crawl or an
        # unattended telemetry poll forever. Override via REDFISH_HTTP_TIMEOUT.
        timeout = float(env_first("REDFISH_HTTP_TIMEOUT", "IDRAC_HTTP_TIMEOUT", default="30"))

        # Reuse one pooled keep-alive connection across GETs (see _http_session):
        # opening a fresh TLS connection per request wedges fragile BMCs.
        session = self._http_session()

        get_kwargs = {"verify": self._is_verify_cert, "timeout": timeout}
        if self.x_auth is not None:
            headers.update({'X-Auth-Token': self.x_auth})
        else:
            get_kwargs["auth"] = (self._username, self._password)

        # CLIENT span for the BMC call (no-op unless tracing is enabled); the BMC
        # renders as one inferred downstream service via peer.service.
        with tracing.client_span(req, "GET") as span:
            try:
                response = session.get(req, headers=headers, **get_kwargs)
            except Exception as exc:  # timeout / connection error → failed span
                tracing.record_exception(span, exc)
                raise
            tracing.record_response(span, response.status_code)
            return response

    def get_job(self,
                job_id: str,
                data_type: Optional[str] = "json",
                do_async: Optional[bool] = False) -> dict:
        """Query information for particular job, per the connection's profile.

        Same-signature delegate: the Dell profile reads the OEM
        ``/Oem/Dell/Jobs`` queue (the body that used to live here); a non-Dell
        connection reads the DMTF ``TaskService`` task instead, so the OEM URL
        never fires at a foreign BMC.

        :param job_id: iDRAC job_id JID_744718373591
        :param do_async: note async will subscribe to an event loop.
        :param data_type: json or xml
        :return: the job payload dict.
        """
        return self.vendor_profile.get_job(
            self, job_id, data_type=data_type, do_async=do_async)

    @staticmethod
    def job_id_from_respond(response) -> str:
        """Try to parse a Dell ``JID_`` job id from an HTTP respond body.

        The scrape body moved out of the neutral ``RedfishManager`` into
        ``DellProfile._scrape_job_id`` (it is a Dell literal); this Dell-side
        delegate keeps the existing ``self.job_id_from_respond`` call sites
        working unchanged.

        :param response: requests.models.Response
        :return: str: a job id or empty string
        """
        from .dell_profile import DellProfile
        return DellProfile._scrape_job_id(response)

    @staticmethod
    def update_progress(resp_data, old_value):
        """Get a percent computed from respond, if something dodgy
        will return old value.
        :param resp_data: response
        :param old_value:  old value
        :return:
        """
        # update percent_done and progress bar
        if REDFISH_JSON.PercentComplete in resp_data:
            try:
                percent_done = int(resp_data[REDFISH_JSON.PercentComplete])
                return percent_done
            except TypeError:
                pass

        return old_value

    def default_error_handler(
            self, response: requests.models.Response) -> RedfishApiRespond:
        """Normalize a Redfish response per the connection's vendor profile.

        Same-signature delegate — the Dell decode body (the 201=Created row and
        the typed raises carrying the parsed :class:`RedfishError` envelope,
        see docs/external/redfish-error-contract.md) moved to
        ``DellProfile.error_handler``; a non-Dell connection resolves the DMTF
        decode instead. The profile records the parsed envelope on this
        manager (``self._redfish_error``) before raising, as before.

        :param response: the Redfish HTTP response to classify.
        :return: the mapped ``RedfishApiRespond`` for a 2xx success code.
        :raises AuthenticationFailed: on HTTP 401, carrying the parsed error.
        :raises RedfishForbidden: on HTTP 403, carrying the parsed error.
        :raises ResourceNotFound: on HTTP 404, carrying the parsed error.
        :raises UnexpectedResponse: on any other error code, carrying the parsed error.
        """
        return self.vendor_profile.error_handler(response, manager=self)

    def check_api_version(self):
        """Check Dell LLC Service API set
        :return:
        """
        headers = {}
        headers.update(self.json_content_type)
        r = f"{self._default_method}" \
            f"{self.redfish_ip}" \
            f"{REDFISH_API.IDRAC_DELL_MANAGERS}" \
            f"{REDFISH_API.IDRAC_LLC}"

        # r = f"{self._default_method}" \
        #     f"{self.redfish_ip}" \
        #     f"{RedfishApi.Version}"

        # print("Sending request {}", r)
        # response = self.api_get_call(r, headers)
        # # print("Response:", response.text)

        response = self.api_get_call(r, headers)
        if response.status_code == 404:
            r = f"{self._default_method}" \
                f"{self.redfish_ip}" \
                f"{RedfishApi.Version}"
            response = self.api_get_call(r, headers)
        self.default_error_handler(response)

        data = response.json()
        self.api_endpoints = data
        if REDFISH_JSON.Actions in self.api_endpoints:
            actions = self.api_endpoints[REDFISH_JSON.Actions]
            action_keys = actions.keys()
            self.action_targets = [actions[k]['target'] for k in action_keys]

        return self.api_endpoints, self.action_targets

    @staticmethod
    @abstractmethod
    def default_json_printer(
            json_data,
            sort: Optional[bool] = True,
            indents: Optional[int] = 4):
        """default json stdout printer, it mainly used for debug.
        :param json_data:
        :param indents:
        :param sort:
        :return:
        """
        if isinstance(json_data, requests.models.Response):
            json_data = json_data.json()

        if isinstance(json_data, str):
            json_raw = json.dumps(
                json.loads(json_data), sort_keys=sort, indent=indents
            )
        else:
            json_raw = json.dumps(
                json_data, sort_keys=sort, indent=indents
            )
        print(json_raw)

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

        if REDFISH_JSON.Actions not in json_data:
            return unfiltered_actions, full_redfish_names

        redfish_actions = json_data[REDFISH_JSON.Actions]
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
        if REDFISH_JSON.Members not in json_data:
            if REDFISH_JSON.Actions in json_data:
                return cls.discover_redfish_actions(cls, json_data)
            else:
                return action_dict

        member_data = json_data[REDFISH_JSON.Members]
        for m in member_data:
            if isinstance(m, dict):
                if REDFISH_JSON.Actions in m.keys():
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

    @cached_property
    def version_api(self, data_type: Optional[str] = "json") -> bool:
        """Return true if IDRAC version 6.0 i.e. a new version.

        :param data_type: content type to request; json or xml.
        :return: True when the iDRAC firmware is 6.00.00.00 or newer, else False.
        """
        headers = {}
        if data_type == "json":
            headers.update(self.json_content_type)
        r = f"{self._default_method}{self.redfish_ip}/redfish/v1/Managers" \
            f"/iDRAC.Embedded.1?$select=FirmwareVersion"
        response = self.api_get_call(r, headers)
        self.default_error_handler(response)
        data = response.json()
        if 'FirmwareVersion' in data:
            fw = data["FirmwareVersion"]
            self.logger.info(f"IDRAC firmware {fw}")
            return int(data["FirmwareVersion"].replace(".", "")) >= 6000000

        return False

    def select_cert(self) -> CommandResult:
        """Return cert
        :return:
        """
        r = f"{self.idrac_members}/Attributes"
        return self.base_query(r, select_target="SecurityCertificate.*")

    def select_network_adapters(self) -> CommandResult:
        """Return the BMC NICs
        :return:
        """
        r = f"{self.idrac_members}/Attributes"
        return self.base_query(r, select_target="NIC.*")

    def select_info(self) -> CommandResult:
        """Return server info
        :return:
        """
        r = f"{self.idrac_members}/Attributes"
        return self.base_query(r, select_target="ServerInfo.*")

    def bios_version(self) -> CommandResult:
        """Return bios version
        :return:
        """
        r = f"{self.idrac_manage_servers}/Attributes"
        return self.base_query(r, select_target="Attributes/SystemBiosVersion")

    def server_model_name(self) -> CommandResult:
        """Return server model name
        :return:
        """
        r = f"{self.idrac_manage_servers}/Attributes"
        return self.base_query(r, select_target="Attributes/SystemModelName")

    def select_server_info(self) -> CommandResult:
        """Return the BMC certificate
        :return:
        """
        r = f"{self.idrac_manage_servers}/Attributes"
        return self.base_query(r, select_target="Info.*")

    @staticmethod
    def filter_attribute(cls,
                         json_data,
                         attr_filter: Optional[str]):
        """Filter attribute from json_data
        :param cls:
        :param json_data:
        :param attr_filter:
        :return:
        """
        if attr_filter is not None and len(attr_filter) > 0 and 'Attributes' in json_data:
            attr_filter = attr_filter.strip()
            if "," in attr_filter:
                attr_filters = attr_filter.split(",")
                if len(attr_filters) > 0:
                    json_data = dict((a, json_data['Attributes'][attr])
                                     for attr in json_data['Attributes'] for a in attr_filters
                                     if a.lower() in attr.lower())
            else:
                json_data = dict((attr, json_data['Attributes'][attr])
                                 for attr in json_data['Attributes']
                                 if attr_filter.lower() in attr.lower())
        return json_data

    @simulate_http_faults(_simulated_connection_error, _simulated_read_timeout)
    def api_delete_call(
            self, req, hdr: Dict) -> requests.models.Response:
        """Make api request for delete method.
        :param req: request
        :param hdr: http header dict that will append to HTTP/HTTPS request.
        :return: request.
        """
        headers = {}
        headers.update(self.content_type)
        if hdr is not None:
            headers.update(hdr)

        if self.x_auth is not None:
            headers.update({'X-Auth-Token': self.x_auth})
            request_call = functools.partial(
                requests.delete,
                req,
                verify=self._is_verify_cert,
                headers=headers,
            )
        else:
            request_call = functools.partial(
                requests.delete,
                req,
                verify=self._is_verify_cert,
                auth=(self._username, self._password),
                headers=headers,
            )
        return tracing.traced_request(req, "DELETE", request_call)

    @simulate_http_faults(_simulated_connection_error, _simulated_read_timeout)
    def api_post_call(
            self, req: str, payload: str, hdr: dict) -> requests.models.Response:
        """Make HTTP post request.
        :param req: path to a path request
        :param payload:  json payload
        :param hdr: header that will append.
        :return: response.
        """
        headers = {}
        headers.update(self.content_type)
        if hdr is not None:
            headers.update(hdr)

        if self.x_auth is not None:
            headers.update({'X-Auth-Token': self.x_auth})
            request_call = functools.partial(
                requests.post,
                req,
                data=payload,
                verify=self._is_verify_cert,
                headers=headers,
            )
        else:
            request_call = functools.partial(
                requests.post,
                req,
                data=payload,
                verify=self._is_verify_cert,
                headers=headers,
                auth=(self._username, self._password),
            )
        return tracing.traced_request(req, "POST", request_call)

    @simulate_http_faults_async(_simulated_connection_error, _simulated_read_timeout)
    async def api_async_post_call(
            self, loop, req: str, payload: str, hdr: Dict):
        """Make post api request either with x-auth authentication header or redfish_ctl.
        :param loop:  asyncio event loop
        :param req:  request
        :param payload:  json payload
        :param hdr: http header dict that will append to HTTP/HTTPS request.
        :return: request.
        """
        headers = {}
        headers.update(self.content_type)
        if hdr is not None:
            headers.update(hdr)

        if self.x_auth is not None:
            request_call = functools.partial(
                requests.post,
                req,
                data=payload,
                verify=self._is_verify_cert,
                headers=headers,
            )
        else:
            request_call = functools.partial(
                requests.post,
                req,
                data=payload,
                headers=headers,
                verify=self._is_verify_cert,
                auth=(self._username, self._password),
            )
        return loop.run_in_executor(
            None,
            tracing.traced_request_callable(req, "POST", request_call),
        )

    async def api_async_patch_until_complete(
            self, r: str,
            payload: str,
            hdr: Dict,
            loop=None,
            expected: Optional[int] = 204,
            ignore_error_code: Optional[int] = 0
    ) -> Tuple[requests.models.Response, RedfishApiRespond]:
        """Make async patch api request until completion , it issues post with x-auth
        authentication header or redfish_ctl. Caller can use this in asyncio routine.

        :param expected:
        :param ignore_error_code:
        :param r: request.
        :param hdr: http header.
        :param loop: asyncio loop
        :param payload: json payload
        :return:
        """
        if loop is None:
            loop = self._event_loop()
        response = await self.api_async_patch_call(loop, r, payload, hdr)
        api_respond_status = await self.async_default_patch_success(
            await response, expected=expected, ignore_error_code=ignore_error_code)
        return await response, api_respond_status

    async def api_async_delete_until_complete(
            self, r: str,
            payload: str,
            hdr: Dict,
            loop=None,
            expected: Optional[int] = 204,
            ignore_error_code: Optional[int] = 0
    ) -> Tuple[requests.models.Response, RedfishApiRespond]:
        """Make async patch api request until completion , it issues post with x-auth
        authentication header or redfish_ctl. Caller can use this in asyncio routine.

        :param expected:
        :param ignore_error_code:
        :param r: request.
        :param hdr: http header.
        :param loop: asyncio loop
        :param payload: json payload
        :return:
        """
        if loop is None:
            loop = self._event_loop()
        response = await self.api_async_delete_call(loop, r, payload, hdr)
        api_respond_status = await self.async_default_delete_success(
            await response, expected=expected, ignore_error_code=ignore_error_code)
        return await response, api_respond_status

    async def api_async_post_until_complete(
            self, r: str,
            payload: str,
            hdr: Dict,
            loop=None,
            expected: Optional[int] = 204,
            ignore_error_code: Optional[int] = 0
    ) -> Tuple[requests.models.Response, RedfishApiRespond]:
        """Make async post api request until completion , it issues post with x-auth
        authentication header or redfish_ctl. Caller can use this in asyncio routine.

        :param expected:
        :param r: request.
        :param hdr: http header
        :param ignore_error_code: error code that we need ignore.
        :param loop: asyncio loop
        :param payload: json payload
        :return:
        """
        if loop is None:
            loop = self._event_loop()
        response = await self.api_async_post_call(loop, r, payload, hdr)
        api_respond_status = await self.async_default_post_success(
            await response, ignore_error_code=ignore_error_code, expected=expected
        )
        return await response, api_respond_status

    @simulate_http_faults(_simulated_connection_error, _simulated_read_timeout)
    def api_patch_call(
            self, req: str, payload: str, hdr: dict, ) -> requests.models.Response:
        """Make api patch request.
        :param req: path to a path request
        :param payload: json payload
        :param hdr: header that will append.
        :return: response.
        """
        headers = {}
        headers.update(self.content_type)
        if hdr is not None:
            headers.update(hdr)

        if self.x_auth is not None:
            headers.update({'X-Auth-Token': self.x_auth})
            request_call = functools.partial(
                requests.patch,
                req,
                data=payload,
                verify=self._is_verify_cert,
                headers=headers,
            )
        else:
            request_call = functools.partial(
                requests.patch,
                req,
                data=payload,
                verify=self._is_verify_cert,
                headers=headers,
                auth=(self._username, self._password),
            )
        return tracing.traced_request(req, "PATCH", request_call)

    @simulate_http_faults_async(_simulated_connection_error, _simulated_read_timeout)
    async def api_async_patch_call(
            self, loop, req, payload: str, hdr: Dict):
        """Make async post api request either with
        x-auth authentication header or redfish_ctl.

        :param loop:  asyncio event loop
        :param req:  request
        :param payload:  json payload
        :param hdr: http header dict that will append to HTTP/HTTPS request.
        :return: request.
        """
        headers = {}
        headers.update(self.content_type)
        if hdr is not None:
            headers.update(hdr)

        if self.x_auth is not None:
            request_call = functools.partial(
                requests.patch,
                req,
                data=payload,
                verify=self._is_verify_cert,
                headers=headers,
            )
        else:
            request_call = functools.partial(
                requests.patch,
                req,
                data=payload,
                verify=self._is_verify_cert,
                headers=headers,
                auth=(self._username, self._password),
            )
        return loop.run_in_executor(
            None,
            tracing.traced_request_callable(req, "PATCH", request_call),
        )

    @simulate_http_faults_async(_simulated_connection_error, _simulated_read_timeout)
    async def api_async_delete_call(
            self, loop, req, payload: str, hdr: Dict):
        """Make async delete api request either with
        x-auth authentication header or redfish_ctl.

        :param loop:  asyncio event loop
        :param req:  request
        :param payload:  json payload
        :param hdr: http header dict that will append to HTTP/HTTPS request.
        :return: request.
        """
        headers = {}
        headers.update(self.content_type)
        if hdr is not None:
            headers.update(hdr)

        if self.x_auth is not None:
            request_call = functools.partial(
                requests.delete,
                req,
                data=payload,
                verify=self._is_verify_cert,
                headers=headers,
            )
        else:
            request_call = functools.partial(
                requests.delete,
                req,
                data=payload,
                verify=self._is_verify_cert,
                headers=headers,
                auth=(self._username, self._password),
            )
        return loop.run_in_executor(
            None,
            tracing.traced_request_callable(req, "DELETE", request_call),
        )

    def read_api_respond(
            self,
            response: requests.models.Response,
            expected: Optional[int] = 204,
            ignore_error_code: Optional[int] = 0) -> RedfishApiRespond:
        """Default success handler,  Check for status code.
        In case of critical error raise exception. If exception expected
        i.e. operation canceled.

        Expected allow to overwrite if API return expect something different
        200, 201, 202, 204

        :param response: requests.models.Response: responses
        :param ignore_error_code: error code to ignore.
        :param expected:  Option status code that we caller consider success.
        :return: RedfishApiRespond if httm method request succeed
        :raise RedfishException if HTTP method failed.
        """
        # if location in the header , job created
        self.logger.debug(f"read_api_respond response code {response.status_code}")

        if response.headers is not None \
                and RedfishJsonSpec.Location in response.headers:
            return RedfishApiRespond.AcceptedTaskGenerated

        if response.status_code == expected:
            return self._http_code_mapping[response.status_code]

        if ignore_error_code > 0 and ignore_error_code == response.status_code:
            return self._http_code_mapping[response.status_code]

        if 200 <= response.status_code < 300:
            return self._http_code_mapping[response.status_code]

        # Normalize the write-path error per the Redfish error contract: every
        # raised exception carries the parsed RedfishError envelope (status,
        # error.code, @Message.ExtendedInfo), never a flattened string. 405/409
        # keep returning RedfishApiRespond.Error (the caller reads the envelope
        # from self._redfish_error), so the return contract is unchanged.
        self._redfish_error = IDracManager.parse_error(response)
        if 300 <= response.status_code < 500:
            if response.status_code == 400:
                raise RedfishException(self._redfish_error)
            if response.status_code == 401:
                raise RedfishUnauthorized(self._redfish_error)
            if response.status_code == 403:
                raise RedfishForbidden(self._redfish_error)
            if response.status_code == 404:
                raise ResourceNotFound(self._redfish_error)
            return RedfishApiRespond.Error
        raise RedfishException(self._redfish_error)

    def default_patch_success(
            self,
            response: requests.models.Response,
            expected: Optional[int] = 202,
            ignore_error_code: Optional[int] = 0) -> RedfishApiRespond:
        """Default delete success handler,  Check for status code.
        and raise exception.  Default handler to check post
        request respond.

        :param response: HTTP response
        :param ignore_error_code: error code to ignore.
        :param expected:  Option status code that we caller consider success.
        :return:
        :raise: DeleteRequestFailed if POST Method failed
        """
        return self.read_api_respond(
            response, expected=expected, ignore_error_code=ignore_error_code
        )

    def default_post_success(
            self,
            response: requests.models.Response,
            expected: Optional[int] = 200,
            ignore_error_code: Optional[int] = 0) -> RedfishApiRespond:
        """Default post success handler. Map the response status or raise on failure.

        :param response: HTTP response.
        :param expected: status code the caller considers success.
        :param ignore_error_code: HTTP status code to treat as success.
        :return: RedfishApiRespond mapped from the response status.
        :raise RedfishException: if the response reports a failure status.
        """
        return self.read_api_respond(
            response, expected=expected, ignore_error_code=ignore_error_code
        )

    def default_delete_success(
            self,
            response: requests.models.Response,
            expected: Optional[int] = 200,
            ignore_error_code: Optional[int] = 0) -> RedfishApiRespond:
        """Default delete success handler,  Check for status code.
        and raise exception.  Default handler to check post
        request respond.

        :param response: HTTP response
        :param ignore_error_code: error code to ignore.
        :param expected:  Option status code that we caller consider success.
        :return:
        :raise; DeleteRequestFailed if POST Method failed
        """
        return self.read_api_respond(
            response, expected=expected, ignore_error_code=ignore_error_code
        )

    async def async_default_post_success(
            self,
            response: requests.models.Response,
            expected: Optional[int] = 204,
            ignore_error_code: Optional[int] = 0) -> RedfishApiRespond:
        """Default error handler, for post
        :param expected:
        :param response: response HTTP response.
        :param ignore_error_code: ignore HTTP statue error.
        :return: True or False and if failed raise exception
        :raise  PostRequestFailed
        """
        return self.read_api_respond(
            response, expected=expected, ignore_error_code=ignore_error_code
        )

    async def async_default_delete_success(
            self,
            response: requests.models.Response,
            expected: Optional[int] = 204,
            ignore_error_code: Optional[int] = 0) -> RedfishApiRespond:
        """Default error handler, for post
        :param ignore_error_code:
        :param expected:
        :param response: response HTTP response.
        :return: True or False and if failed raise exception
        :raise  PostRequestFailed
        """
        return self.read_api_respond(
            response, expected=expected, ignore_error_code=ignore_error_code
        )

    async def async_default_patch_success(
            self, response: requests.models.Response,
            expected: Optional[int] = 204,
            ignore_error_code: Optional[int] = 0) -> RedfishApiRespond:
        """Default error handler for patch http method.
        :param expected:
        :param response: response HTTP response.
        :param ignore_error_code: ignore HTTP statue error.
        :return: True or False and if failed raise exception
        """
        return self.read_api_respond(
            response, expected=expected, ignore_error_code=ignore_error_code
        )

    @staticmethod
    def _redact_sensitive_payload(payload):
        """Return a copy of a request payload with secret-like fields masked.

        :param payload: JSON-compatible request payload.
        :return: payload copy safe for debug logging.
        """
        sensitive_exact = {"apikey", "api_key", "accesskey", "access_key"}
        sensitive_suffixes = ("password", "secret", "token")

        if isinstance(payload, dict):
            redacted = {}
            for key, value in payload.items():
                key_text = str(key).lower()
                if key_text in sensitive_exact or key_text.endswith(sensitive_suffixes):
                    redacted[key] = "********"
                else:
                    redacted[key] = IDracManager._redact_sensitive_payload(value)
            return redacted
        if isinstance(payload, list):
            return [
                IDracManager._redact_sensitive_payload(value)
                for value in payload
            ]
        return payload


    def base_request_respond(
            self,
            resource: str,
            method: HTTPMethod,
            payload: Optional[dict] = None,
            do_async: Optional[bool] = False,
            data_type: Optional[str] = "json",
            expected_status: Optional[int] = 200,
            ignore_error_code: Optional[int] = 0) -> tuple[CommandResult, RedfishApiRespond]:
        """A base http post/patch/delete method.

        Return result as command result named tuples and RedfishApiRespond
        reflect API respond enum based on HTTP/HTTPS status replay

        :param method: http method POST/PATCH etc.
        :param ignore_error_code: error code that don't consider an error.
                                  main for case when job , task canceled.
                                  and we don't consider 404 not found as error.
        :param resource:  a request to api,  /redfish/v1/
        :param payload: a json payload if payload is empty caller need pass empty dict
        :param do_async: for asynced request.
        :param data_type: a data-type json/xml
        :param expected_status: expected status code depend on patch msg.
        :return: Tuple of CommandResult, RedfishApiRespond
        """
        headers = {}
        if data_type == "json":
            headers.update(self.json_content_type)

        pd = payload if payload is not None else {}
        log_payload = self._redact_sensitive_payload(pd)

        self.logger.debug(f"Issuing {method} request to "
                          f"resource: {resource}, "
                          f"payload: {json.dumps(log_payload)}")

        err = None
        response = None
        api_resp = RedfishApiRespond.Error
        self._redfish_error = None
        try:
            r = f"{self._default_method}{self.redfish_ip}{resource}"
            if not do_async:
                if method == HTTPMethod.PATCH:
                    response = self.api_patch_call(
                        r, json.dumps(pd), headers
                    )
                    api_resp = self.default_patch_success(
                        response, expected=expected_status,
                        ignore_error_code=ignore_error_code
                    )
                if method == HTTPMethod.POST:
                    response = self.api_post_call(
                        r, json.dumps(pd), headers
                    )
                    api_resp = self.default_post_success(
                        response, expected=expected_status,
                        ignore_error_code=ignore_error_code
                    )
                if method == HTTPMethod.DELETE:
                    response = self.api_delete_call(
                        r, headers
                    )
                    api_resp = self.default_delete_success(
                        response, expected=expected_status,
                        ignore_error_code=ignore_error_code
                    )
            else:
                loop = self._event_loop()
                # The api_async_*_until_complete helpers return
                # (Response, RedfishApiRespond) - the same order the sync branch
                # above binds. Unpacking them the other way round bound the enum
                # to `response` and the Response object to `api_resp`, so on the
                # async path parse_json_respond_msg() received an enum and
                # api_success_msg() did a dict lookup with a Response as the key,
                # raising KeyError AFTER the write had already been sent.
                if method == HTTPMethod.PATCH:
                    response, api_resp = loop.run_until_complete(
                        self.api_async_patch_until_complete(
                            r, json.dumps(pd), headers,
                            expected=expected_status,
                            ignore_error_code=ignore_error_code
                        )
                    )
                if method == HTTPMethod.POST:
                    response, api_resp = loop.run_until_complete(
                        self.api_async_post_until_complete(
                            r, json.dumps(pd), headers,
                            expected=expected_status,
                            ignore_error_code=ignore_error_code
                        )
                    )
                if method == HTTPMethod.DELETE:
                    response, api_resp = loop.run_until_complete(
                        self.api_async_delete_until_complete(
                            r, json.dumps(pd), headers,
                            expected=expected_status,
                            ignore_error_code=ignore_error_code
                        )
                    )
        except PatchRequestFailed as pf:
            self.logger.critical(
                pf, exc_info=self._is_debug
            )
            err = pf
        except PostRequestFailed as pf:
            self.logger.critical(
                pf, exc_info=self._is_debug
            )
            err = pf
        except DeleteRequestFailed as pf:
            self.logger.critical(
                pf, exc_info=self._is_debug)
            err = pf

        redfish_resp = None
        if response is not None:
            redfish_resp = self.parse_json_respond_msg(response)

        # if task id available, we fetch task/job id from header
        # and include in return api
        if api_resp == RedfishApiRespond.AcceptedTaskGenerated:
            task_id = self.job_id_from_header(response)
            return CommandResult(
                {"task_id": task_id}, None, None, None), api_resp

        api_resp_msg = self.api_success_msg(api_resp)
        if redfish_resp is not None:
            api_resp_msg.update(api_resp_msg)
        if api_resp == RedfishApiRespond.Error and err is None:
            err = self._redfish_error
            if err is None and response is not None:
                err = self.parse_error(response)

        return CommandResult(api_resp_msg, None, None, err), api_resp

    def base_post(self,
                  resource: str,
                  payload: Optional[dict] = None,
                  do_async: Optional[bool] = False,
                  data_type: Optional[str] = "json",
                  expected_status: Optional[int] = 204,
                  ignore_error_code: Optional[int] = 0) -> tuple[CommandResult, RedfishApiRespond]:
        """Base http post request for redfish remote api.

        Returns CommandResult and data field contain a data payload.
        If such data is present. In most case post doesn't return anything.

        Method return a tuple CommandResult, RedfishApiRespond
        provide option if post accepted , ok status just ok or failed.

        Meanwhile, in case error CommandResult. Error
        store Redfish error if any.

        :param ignore_error_code:
        :param resource: a remote redfish api resource
        :param payload: a json payload
        :param do_async: whether we do asyncio or not
        :param data_type: a default data type json or xml.
        :param expected_status: in case we expect http status that different from spec.
        :return: Tuple[CommandResult, RedfishApiRespond]
        :raise: PostRequestFailed for all error that we can't handle.
                i.e. error like api return are not exception.
        """
        return self.base_request_respond(
            resource, HTTPMethod.POST, payload=payload,
            do_async=do_async, data_type=data_type,
            expected_status=expected_status, ignore_error_code=ignore_error_code,
        )

    def base_patch(
            self,
            resource: str,
            payload: Optional[dict] = None,
            do_async: Optional[bool] = False,
            data_type: Optional[str] = "json",
            expected_status: Optional[int] = 204,
            ignore_error_code: Optional[int] = 0) -> tuple[CommandResult, RedfishApiRespond]:
        """Base http post request for redfish remote api.

        Returns CommandResult and data field contain a data payload.
        If such data is present. In most case post doesn't return anything.

        Method return a tuple CommandResult, RedfishApiRespond
        provide option if post accepted , ok status just ok or failed.

        Meanwhile, in case error CommandResult. Error
        store Redfish error if any.

        :param ignore_error_code:
        :param resource: a remote redfish api resource
        :param payload: a json payload
        :param do_async: whether we do asyncio or not
        :param data_type: a default data type json or xml.
        :param expected_status: in case we expect http status that different from spec.
        :return: Tuple[CommandResult, RedfishApiRespond]
        :raise: PostRequestFailed for all error that we can't handle.
                i.e. error like api return are not exception.
        """
        return self.base_request_respond(
            resource, HTTPMethod.PATCH, payload=payload,
            do_async=do_async, data_type=data_type,
            expected_status=expected_status, ignore_error_code=ignore_error_code,
        )

    def base_delete(
            self,
            resource: str,
            payload: Optional[dict] = None,
            do_async: Optional[bool] = False,
            data_type: Optional[str] = "json",
            expected_status: Optional[int] = 204,
            ignore_error_code: Optional[int] = 0) -> tuple[CommandResult, RedfishApiRespond]:
        """Base http delete request for redfish remote api.

        Returns CommandResult and data field contain a data payload.
        If such data is present. In most case post doesn't return anything.

        Method return a tuple CommandResult, RedfishApiRespond
        provide option if post accepted , ok status just ok or failed.

        Meanwhile, in case error CommandResult. Error
        store Redfish error if any.

        :param ignore_error_code:
        :param resource: a remote redfish api resource
        :param payload: a json payload
        :param do_async: whether we do asyncio or not
        :param data_type: a default data type json or xml.
        :param expected_status: in case we expect http status that different from spec.
        :return: Tuple[CommandResult, RedfishApiRespond]
        :raise: PostRequestFailed for all error that we can't handle.
                i.e. error like api return are not exception.
        """
        return self.base_request_respond(
            resource, HTTPMethod.DELETE, payload=payload,
            do_async=do_async, data_type=data_type,
            expected_status=expected_status, ignore_error_code=ignore_error_code,
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
                                 action: Optional[RedfishAction],
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

    def reboot(
            self,
            do_watch: Optional[bool] = False,
            power_state_attr: Optional[str] = "PowerState",
            default_reboot_type: Optional[ResetType] = ResetType.ForceRestart.value) -> CommandResult:
        """Reboot a chassis, if chassis in power down state.

        Reboot on power down state is no op, method return
        chassis data. caller need check CommandResult data
        and if required Change power state.

        :param do_watch: if reboot respond with task_id, do watch
                         passed to reboot and block reboot complete.
        :param default_reboot_type: ResetType.ForceRestart: type reboot.
        :param power_state_attr:  is attribute method check
                to determine chassis up or in power done state.
        :return:
        """
        # state of chassis
        cmd_chassis = self.sync_invoke(
            ApiRequestType.ChassisQuery,
            "chassis_service_query",
            data_filter=power_state_attr
        )

        if cmd_chassis.error is not None:
            self.logger.info(
                "Failed to fetch a chassis power state. Chassis return error."
            )
            return cmd_chassis

        if isinstance(cmd_chassis.data, dict) and REDFISH_JSON.PowerState in cmd_chassis.data:
            pd_state = cmd_chassis.data[power_state_attr]
            if pd_state.lower() == 'on':
                return self.sync_invoke(
                    ApiRequestType.ComputerSystemReset, "reboot",
                    reset_type=default_reboot_type,
                    do_wait=do_watch
                )
            else:
                self.logger.info(
                    f"Can't reboot a host, "
                    f"chassis power state in {pd_state} state."
                )
                return CommandResult({}, None, None, None)
        else:
            self.logger.info(
                "Failed to acquire current power state."
            )

        return cmd_chassis

    @cached_property
    def idrac_firmware(self) -> str:
        """Shared method return the BMC firmware
        :return: str: firmware.
        """
        api_return = self.base_query(self.idrac_members,
                                     key=REDFISH_JSON.FirmwareVersion)
        return api_return.data

    def idrac_last_reset(self) -> datetime:
        """Shared method returns the BMC last reset time as datatime
        :return: datetime
        """
        idrac_reset_time = None
        api_return = self.base_query(self.idrac_members,
                                     key=REDFISH_JSON.LastResetTime)
        try:
            idrac_reset_time = datetime.fromisoformat(api_return.data)
        except ValueError as ve:
            self.logger.error(ve)
        return idrac_reset_time

    def idrac_current_time(self) -> datetime:
        """Shared method return the BMC current time, if the BMC
        return none ISO format
        :return: datetime
        """
        idrac_time = None
        api_return = self.base_query(self.idrac_members,
                                     key=REDFISH_JSON.Datatime)
        try:
            idrac_time = datetime.fromisoformat(api_return.data)
        except ValueError as ve:
            self.logger.error(ve)
        return idrac_time

    @staticmethod
    def local_time_iso():
        """return local time in iso format
        :return: the BMC local time
        """
        current_date = datetime.now()
        return current_date.isoformat()

    def idrac_time_offset(self):
        """
        :return:  the BMC time zone
        """
        api_resp = self.base_query(self.idrac_members,
                                   key=REDFISH_JSON.DateTimeLocalOffset)
        return api_resp.data

    @cached_property
    def idrac_manage_chassis(self) -> str:
        """Shared method return the BMC managed chassis list as json
        :return: str: manage chassis i.e. /redfish/v1/Chassis/System.Embedded.1
        """
        api_resp = self.base_query(self.idrac_members, key=REDFISH_JSON.Links)
        if api_resp.data is not None and REDFISH_JSON.ManageChassis in api_resp.data:
            if isinstance(api_resp.data, dict):
                manage_chassis = api_resp.data[REDFISH_JSON.ManageChassis]
                self._manage_chassis_obs = manage_chassis
                return self.value_from_json_list(
                    manage_chassis, REDFISH_JSON.Data_id
                )
        else:
            self.logger.error("")
        return ""

    @cached_property
    def idrac_managers_count(self) -> str:
        """Return manager count. typically it 1
        :return:
        """
        cmd_result = self.base_query(f"{REDFISH_API.IDRAC_MANAGER}")
        return cmd_result.data["Members@odata.count"]

    @cached_property
    def idrac_manager_version(self) -> str:
        """Remote BMC version.
        :return:
        """
        cmd_result = self.base_query(
            f"{REDFISH_API.IDRAC_MANAGER}", key=REDFISH_JSON.Members)

        # the tool only manages one instance.
        member_list = cmd_result.data
        if len(member_list) > 1:
            logging.warning("idrac manage more than one entity")
        elif len(member_list) == 1:
            member = member_list[-1]
            if RedfishJson.Data_id in member:
                member_target = member[RedfishJson.Data_id]
                cmd_result = self.base_query(
                    member_target,
                    key=REDFISH_JSON.IDracFirmwareVersion
                )
                return cmd_result.data
        else:
            raise ResourceNotFound("no iDRAC manager member found")

    @cached_property
    def idrac_members(self) -> str:
        """Shared method return the BMC managed member servers list as json
        /redfish/v1/Managers/iDRAC.Embedded.1

        Upon first call , result cached all follow-up call will return cached result.
        :return:
        """
        cmd_result = self.base_query(f"{REDFISH_API.IDRAC_MANAGER}", key=REDFISH_JSON.Members)
        return self.value_from_json_list(cmd_result.data, REDFISH_JSON.Data_id)

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
        return [m[REDFISH_JSON.Data_id] for m in members
                if isinstance(m, dict) and isinstance(m.get(REDFISH_JSON.Data_id), str)]

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
        cmd_result = self.base_query(RedfishApi.Systems, key=REDFISH_JSON.Members)
        return self._member_ids(cmd_result.data)

    def discover_manager_ids(self) -> list:
        """Return ALL Manager ids from ``/redfish/v1/Managers`` (e.g. BMC_0, HGX_BMC_0).

        Companion to :meth:`discover_computer_system_ids` for boxes with more
        than one BMC; ``idrac_members`` only yields a single (last) manager.

        :return: the list of Manager ``@odata.id`` paths.
        """
        cmd_result = self.base_query(RedfishApi.Managers, key=REDFISH_JSON.Members)
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

    def computer_system_id(self):
        """alias name for idrac_manage_servers to match v6.0 docs
        :return: str: computer_system_id "/redfish/v1/Systems/System.Embedded.1"
        """
        return self.idrac_manage_servers

    @cached_property
    def idrac_manage_servers(self) -> str:
        """Return the managed (host) ComputerSystem path, e.g.
        /redfish/v1/Systems/System.Embedded.1. Cached after the first call.

        Resolves via the manager's ManagerForServers link. On a multi-system host
        (e.g. a GB300 exposing System_0 + the NVIDIA HGX baseboard) that link,
        taken from the last Managers member, can land on a non-host baseboard, so
        when /redfish/v1/Systems has more than one member we prefer the host
        system -- the one exposing a Bios/Boot link. Single-system hosts (Dell)
        and hosts without a reachable Systems collection keep the original result.

        :return: the managed host ComputerSystem path (empty string if unresolved).
        """
        resolved = ""
        api_resp = self.base_query(self.idrac_members, key=REDFISH_JSON.Links)
        if api_resp.data is not None and REDFISH_JSON.ManagerServers in api_resp.data:
            if isinstance(api_resp.data, dict):
                manage_servers = api_resp.data[REDFISH_JSON.ManagerServers]
                self._manage_servers_obs = manage_servers
                resolved = self.value_from_json_list(
                    manage_servers, REDFISH_JSON.Data_id
                )
        else:
            self.logger.error("")
        try:
            system_ids = self.discover_computer_system_ids()
        except Exception:
            system_ids = []
        if len(system_ids) > 1:
            host = self._host_system(system_ids)
            if host:
                return host
        return resolved

    @cached_property
    def idrac_id(self):
        """Shared method return the manager id, i.e. System.Embedded.1
        id cached all follow-up calls and will return cached result.
        :return:
        """
        self.base_query(self.idrac_manage_servers, key=REDFISH_JSON.Id)
        api_resp = self.base_query(self.idrac_manage_servers, key=REDFISH_JSON.Id)
        if api_resp is None:
            self.logger.critical(f"failed obtain {REDFISH_JSON.Id}")
        return api_resp.data

    @staticmethod
    def base_parser(is_async: Optional[bool] = True,
                    is_file_save: Optional[bool] = True,
                    is_expanded: Optional[bool] = True,
                    is_remote_share: Optional[bool] = False,
                    is_reboot: Optional[bool] = False):
        """
        This redfish_ctl optional parser for all sub command
        that most of command share.
        Each sub-command can add additional optional flags and args.
        :param is_async: will add to optional group async
        :param is_file_save:  will add to optional group arg option save to file
        :param is_expanded:  will add to optional group arg option for expanded query
        :param is_remote_share:  will add remote share for optional group arg
        :param is_reboot:  will add optional reboot for optional arg.
                           ( for cmds that we want to execute and reboot)
        :return:
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
    def schedule_job_request(
            reboot_type: ScheduleJobType,
            start_time_isofmt: Optional[str],
            duration_time: Optional[int]) -> dict:
        """Create a JSON payload for schedule a job, either
        somewhere in future or OnReset.

        :param reboot_type: reboot type, ScheduleJobType
        :param start_time_isofmt: start time for a job in ISO format.
        :param duration_time: duration for a job
        :return:
        """
        if reboot_type == ScheduleJobType.NoReboot:
            pd = {
                REDFISH_JSON.RedfishSettingsApplyTime: {
                    REDFISH_JSON.ApplyTime: JobApplyTypes.InMaintenance,
                    REDFISH_JSON.MaintenanceWindowStartTime: start_time_isofmt,
                    REDFISH_JSON.MaintenanceWindowDuration: duration_time
                }
            }
        elif reboot_type == ScheduleJobType.AutoReboot:
            pd = {
                REDFISH_JSON.RedfishSettingsApplyTime: {
                    REDFISH_JSON.ApplyTime: JobApplyTypes.AtMaintenance,
                    REDFISH_JSON.MaintenanceWindowStartTime: start_time_isofmt,
                    REDFISH_JSON.MaintenanceWindowDuration: duration_time
                }
            }
        elif reboot_type == ScheduleJobType.OnReset:
            pd = {
                REDFISH_JSON.RedfishSettingsApplyTime: {
                    REDFISH_JSON.ApplyTime: JobApplyTypes.OnReset
                }
            }
        elif reboot_type == ScheduleJobType.Immediate:
            pd = {
                REDFISH_JSON.RedfishSettingsApplyTime: {
                    REDFISH_JSON.ApplyTime: JobApplyTypes.Immediate
                }
            }
        else:
            raise InvalidArgumentFormat(
                "Invalid settings apply time.")
        return pd

    @staticmethod
    def make_future_job_ts(start_date: str,
                           start_time: str,
                           is_json_string=False) -> str:
        """Make a future time for a maintenance task.

        Specifically @Redfish.MaintenanceWindow

        It takes start_date in format YYYY-MM-DD
        and starts time in format HH:MM:SS and return
        JSON string time in ISO format.

        "2023-02-08T01:01:01.000001"

       :param start_date: start date for a job YYYY-MM-DD
       :param start_time: a start time for a job HH:MM:SS
       :param is_json_string: return a JSON string when True, else a plain string.
       :return: the future timestamp in ISO 8601 format (JSON-encoded when
           ``is_json_string`` is True).
       :raise: MissingMandatoryArguments if mandatory args missing.
       :raise: InvalidArgumentFormat if format of the input is invalid.
       """
        if start_date is None or len(start_date) == 0:
            raise MissingMandatoryArguments(
                "A maintenance job requires a start date.")

        if start_time is None or len(start_time) == 0:
            raise MissingMandatoryArguments(
                "A maintenance job requires a start time.")
        try:
            local_timestamp = datetime.now()
            ts = f'{start_date}T{start_time}.000001'
            start_timestamp = datetime.fromisoformat(ts)
            if start_timestamp < local_timestamp:
                raise InvalidArgumentFormat(
                    f"Start time is in the past local time "
                    f"{str(local_timestamp)} {str(start_timestamp)}")

            # note we always parse to make sure we can convert.
            json_str = json.dumps(start_timestamp.isoformat())
            if is_json_string:
                return json_str
            else:
                return start_timestamp.isoformat()
        except ValueError as ve:
            raise InvalidArgumentFormat(str(ve))
        except TypeError as te:
            raise InvalidArgumentFormat(str(te))

    def create_apply_time_req(self,
                              apply: str,
                              start_date: str,
                              start_time: str,
                              default_duration):
        """
        The settings apply time and operation apply time annotations
        enable an operation to be performed during a maintenance window
        :param apply: apply-time selector: auto-boot, maintenance, or on-reset.
        :param start_date: a date as string
        :param start_time: a start time for a future job
        :param default_duration: a duration
        :return: the settings apply-time request payload built by
            :meth:`schedule_job_request`.
        :raise InvalidArgumentFormat will raise in case we can't parse args
        :raise ValueError if unknown apply type
        """
        if apply.strip().lower() == "auto-boot":
            start_timestamp = self.make_future_job_ts(start_date, start_time)
            return self.schedule_job_request(
                ScheduleJobType.AutoReboot,
                start_time_isofmt=start_timestamp,
                duration_time=default_duration
            )
        elif apply.strip().lower() == "maintenance":
            start_timestamp = self.make_future_job_ts(start_date, start_time)
            return self.schedule_job_request(
                ScheduleJobType.NoReboot,
                start_time_isofmt=start_timestamp,
                duration_time=default_duration
            )
        elif apply.strip().lower() == "on-reset":
            return self.schedule_job_request(
                ScheduleJobType.OnReset,
                start_time_isofmt=None,
                duration_time=None
            )
        else:
            raise ValueError("Unknown apply time")

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
        return_dict = {
            "Status": self._api_respond_to_string[api_respond]
        }

        if message is not None:
            return_dict[message_key] = message

        return return_dict

    @property
    def power_state(self) -> PowerState:
        """
        :return:
        """
        cmd_result = self.base_query(self.idrac_manage_chassis,
                                     do_async=False,
                                     do_expanded=True)

        if cmd_result.error is not None:
            return PowerState.Unknown

        if REDFISH_JSON.PowerState not in cmd_result.data:
            raise UnexpectedResponse(f"{REDFISH_JSON.PowerState} not present in respond.")

        power_state = cmd_result.data[REDFISH_JSON.PowerState]
        if 'On' in power_state:
            return PowerState.On
        if 'Off' in power_state:
            return PowerState.Off

    def chassis_string_property(self, property_name: str) -> str:
        """ Return chassis string property
        :param property_name:
        :return:
        """
        cmd_result = self.base_query(self.idrac_manage_chassis,
                                     do_async=False,
                                     do_expanded=True)

        if property_name not in cmd_result.data:
            raise UnexpectedResponse(f"{property_name} not present in respond.")

        json_property = cmd_result.data[property_name]
        if not isinstance(json_property, str):
            raise UnexpectedResponse(f"{property_name} must be a string.")

        return cmd_result.data[property_name]

    @cached_property
    def serial(self) -> str:
        """return chassis serial number
        :return: str: chassis serial number
        """
        return self.chassis_string_property("SerialNumber")

    @cached_property
    def chassis_type(self) -> str:
        """return chassis type
        :return: str: chassis type
        """
        return self.chassis_string_property("ChassisType")

    @cached_property
    def chassis_uuid(self) -> str:
        """return chassis uuid
        :return: str: chassis uuid
        """
        return self.chassis_string_property("UUID")

    # def sysmgmt(self):
