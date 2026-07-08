"""

Before you run unit test.
IDRAC_IP=IP
IDRAC_PASSWORD=PASS
IDRAC_USERNAME=root
# set PYTHONWARNINGS as well, so it will not output warning about insecure.
PYTHONWARNINGS=ignore:Unverified HTTPS request

Author Mus spyroot@gmail.com

"""
import json
import os
from unittest import TestCase

import pytest

from idrac_ctl.idrac_manager import CommandResult, IDracManager
from idrac_ctl.idrac_shared import ApiRequestType

# Integration tests: require a reachable iDRAC.
# Skipped automatically unless IDRAC_IP is set (see tests/conftest.py).
pytestmark = pytest.mark.live

class TestFirmware(TestCase):
    redfish_api = None

    @classmethod
    def setUpClass(cls) -> IDracManager:
        redfish_api = IDracManager(idrac_ip=os.environ.get('IDRAC_IP', ''),
                                   idrac_username=os.environ.get('IDRAC_USERNAME', 'root'),
                                   idrac_password=os.environ.get('IDRAC_PASSWORD', ''),
                                   insecure=True,
                                   is_debug=False)
        return redfish_api

    def setUp(self) -> None:
        self.assertTrue(len(os.environ.get('IDRAC_IP', '')) > 0, "IDRAC_IP is none")
        self.assertTrue(len(os.environ.get('IDRAC_USERNAME', '')) > 0, "IDRAC_USERNAME is none")
        self.assertTrue(len(os.environ.get('IDRAC_PASSWORD', '')) > 0, "IDRAC_PASSWORD is none")

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
