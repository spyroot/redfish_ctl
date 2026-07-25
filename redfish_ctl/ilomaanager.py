import argparse
import collections
import functools
import json
import logging
from typing import Dict, Optional, Tuple

from .redfish_manager import (
    CommandResult,
    RedfishManager,
)

class IloManager(RedfishManager):
    """
    IDracManager Class, interact with a Redfish endpoint via REST API interface
    """

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

    _registry = collections.defaultdict(dict)
