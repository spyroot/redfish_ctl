"""

Before you run unit test.
REDFISH_IP=IP
REDFISH_PASSWORD=PASS
REDFISH_USERNAME=root
# set PYTHONWARNINGS as well, so it will not output warning about insecure.
PYTHONWARNINGS=ignore:Unverified HTTPS request

Author Mus spyroot@gmail.com

"""
import json
import os
from unittest import TestCase

import pytest

from redfish_ctl.idrac_manager import CommandResult, IDracManager
from redfish_ctl.redfish_api_common import ApiRequestType

# Integration tests: require a reachable iDRAC.
# Skipped automatically unless REDFISH_IP is set (see tests/conftest.py).
pytestmark = pytest.mark.live

class TestFirmware(TestCase):
    redfish_api = None

    @classmethod
    def setUpClass(cls) -> IDracManager:
        redfish_api = IDracManager(host=os.environ.get('REDFISH_IP', ''),
                                   username=os.environ.get('REDFISH_USERNAME', 'root'),
                                   password=os.environ.get('REDFISH_PASSWORD', ''),
                                   insecure=True,
                                   is_debug=False)
        return redfish_api

    def setUp(self) -> None:
        self.assertTrue(len(os.environ.get('REDFISH_IP', '')) > 0, "REDFISH_IP is none")
        self.assertTrue(len(os.environ.get('REDFISH_USERNAME', '')) > 0, "REDFISH_USERNAME is none")
        self.assertTrue(len(os.environ.get('REDFISH_PASSWORD', '')) > 0, "REDFISH_PASSWORD is none")

    def test_firmware_query(self):
        """

        :return:
        """
        manager = self.setUpClass()
        result = manager.sync_invoke(
            ApiRequestType.FirmwareQuery, "firmware_query")
        self.assertIsInstance(result, CommandResult)
        self.assertIsInstance(result.data, dict)
        try:
            json.dumps(result.data, sort_keys=True, indent=4)
        except Exception as _:
            self.fail("raised exception")
        self.assertTrue('Members' in result.data, "Failed to fetch mandatory key")

    def test_firmware_deep_query(self):
        """

        :return:
        """
        manager = self.setUpClass()
        result = manager.sync_invoke(
            ApiRequestType.FirmwareQuery, "firmware_query", do_deep=True)
        self.assertIsInstance(result, CommandResult)
        self.assertIsInstance(result.data, dict)
        try:
            json.dumps(result.data, sort_keys=True, indent=4)
        except Exception as _:
            self.fail("raised exception")

        self.assertTrue(
            'Members' in result.data,
            f"Failed to fetch mandatory key, keys {result.data.keys()}")
