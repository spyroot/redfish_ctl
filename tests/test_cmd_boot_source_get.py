"""This a unit test for query accounts in redfish/idrac

Before you run unit test.
REDFISH_IP=IP
REDFISH_PASSWORD=PASS
REDFISH_USERNAME=root
# set PYTHONWARNINGS as well, so it will not output warning about insecure.
PYTHONWARNINGS=ignore:Unverified HTTPS request

Author Mus spyroot@gmail.com
"""
import json
import logging
import os
from json import JSONDecodeError
from unittest import TestCase

import pytest

from redfish_ctl.base_manager import CommandBase, CommandResult
from redfish_ctl.command_shared import ApiRequestType

logging.basicConfig()
log = logging.getLogger("LOG")


# Integration tests: require a reachable iDRAC.
# Skipped automatically unless REDFISH_IP is set (see tests/conftest.py).
pytestmark = pytest.mark.live

class TestBootSettingsQuery(TestCase):
    """
    Account cmd unit test.
    """
    redfish_api = None

    @classmethod
    def setUpClass(cls) -> CommandBase:
        redfish_api = CommandBase(
            idrac_ip=os.environ.get('REDFISH_IP', ''),
            idrac_username=os.environ.get('REDFISH_USERNAME', 'root'),
            idrac_password=os.environ.get('REDFISH_PASSWORD', ''),
            insecure=True,
            is_debug=False)
        return redfish_api

    def setUp(self) -> None:
        self.assertTrue(
            len(os.environ.get('REDFISH_IP', '')) > 0, "REDFISH_IP is none")
        self.assertTrue(
            len(os.environ.get('REDFISH_USERNAME', '')) > 0, "REDFISH_USERNAME is none")
        self.assertTrue\
            (len(os.environ.get('REDFISH_PASSWORD', '')) > 0, "REDFISH_PASSWORD is none")

    def test_basic_account_query(self):
        """test basic boot source query
        :return:
        """
        manager = self.setUpClass()
        query_result = manager.sync_invoke(
            ApiRequestType.BootSettingsQuery, "boot_source_query")

        self.assertIsInstance(query_result, CommandResult)
        self.assertIsInstance(query_result.data, dict)
        try:
            json.dumps(query_result.data, sort_keys=True, indent=4)
        except TypeError as _:
            self.fail("raised exception")
        except JSONDecodeError as _:
            self.fail("raised exception")
