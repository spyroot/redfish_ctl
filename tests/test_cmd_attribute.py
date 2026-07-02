"""This a unit test for query attributes in redfish/idrac

Before you run unit test.
IDRAC_IP=IP
IDRAC_PASSWORD=PASS
IDRAC_USERNAME=root
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

from idrac_ctl.idrac_manager import CommandResult, IDracManager
from idrac_ctl.idrac_shared import ApiRequestType
from idrac_ctl.redfish_shared import RedfishJson

logging.basicConfig()
log = logging.getLogger("LOG")


# Integration tests: require a reachable iDRAC.
# Skipped automatically unless IDRAC_IP is set (see tests/conftest.py).
pytestmark = pytest.mark.live

class TestAttribute(TestCase):
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

    def test_basic_attribute_query(self):
        """test basic query
        :return:
        """
        manager = self.setUpClass()
        query_result = manager.sync_invoke(
            ApiRequestType.AttributesQuery, "attribute_inventory")

        self.assertIsInstance(query_result, CommandResult)
        self.assertIsInstance(query_result.data, dict)
        try:
            json.dumps(query_result.data, sort_keys=True, indent=4)
        except TypeError as _:
            self.fail("raised exception")
        except JSONDecodeError as _:
            self.fail("raised exception")

        self.assertTrue(RedfishJson.Attributes in query_result.data,
                        f"Failed to fetch mandatory {RedfishJson.Attributes} key")
        self.assertTrue('AttributeRegistry' in query_result.data,
                        "Failed to fetch mandatory AttributeRegistry")
        self.assertTrue(RedfishJson.Data_id in query_result.data,
                        f"Failed to fetch mandatory {RedfishJson.Data_id} key")

    def test_basic_attribute_query_filter(self):
        """test basic query
        :return:
        """
        manager = self.setUpClass()

        query_result = manager.sync_invoke(
            ApiRequestType.AttributesQuery, "attribute_inventory", attr_filter="ServerTopology")

        self.assertIsInstance(query_result, CommandResult)
        self.assertIsInstance(query_result.data, dict)
        try:
            json.dumps(query_result.data, sort_keys=True, indent=4)
        except TypeError as _:
            self.fail("raised exception")

        self.assertFalse(RedfishJson.Attributes in query_result.data,
                         f"Result should filter out attribute {RedfishJson.Attributes} key")
        self.assertFalse('AttributeRegistry' in query_result.data,
                         "Result should filter out AttributeRegistry")

        self.assertFalse(RedfishJson.Data_id in query_result.data,
                         f"Failed to fetch mandatory {RedfishJson.Data_id} key")

        self.assertFalse("RandomKey" in query_result.data,
                         f"Failed to fetch mandatory {RedfishJson.Data_id} key")

    def test_basic_attr_query_filter(self):
        """test basic query
        :return:
        """
        manager = self.setUpClass()

        query_result = manager.sync_invoke(
            ApiRequestType.AttributesQuery, "attribute_inventory", attr_filter="SystemInfo.1.BootTime")

        self.assertIsInstance(query_result, CommandResult)
        self.assertIsInstance(query_result.data, dict)
        try:
            json.dumps(query_result.data, sort_keys=True, indent=4)
        except TypeError as _:
            self.fail("raised exception")

        self.assertFalse(RedfishJson.Attributes in query_result.data,
                         f"Result should filter out attribute {RedfishJson.Attributes} key")

        self.assertFalse('AttributeRegistry' in query_result.data,
                         "Result should filter out AttributeRegistry")

        self.assertFalse(RedfishJson.Data_id in query_result.data,
                         f"Failed to fetch mandatory {RedfishJson.Data_id} key")

        self.assertFalse("RandomKey" in query_result.data,
                         f"Failed to fetch mandatory {RedfishJson.Data_id} key")

        self.assertTrue("SystemInfo.1.BootTime" in query_result.data,
                        "Failed to retrieve key SystemInfo.1.BootTime key")

    def test_basic_attr_query_filter_save(self):
        """test basic query
        :return:
        """
        manager = self.setUpClass()

        query_result = manager.sync_invoke(
            ApiRequestType.AttributesQuery,
            "attribute_inventory",
            attr_filter="SystemInfo.1.BootTime",
        )

        self.assertIsInstance(query_result, CommandResult)
        self.assertIsInstance(query_result.data, dict)
        try:
            json.dumps(query_result.data, sort_keys=True, indent=4)
        except TypeError as _:
            self.fail("raised exception")

        self.assertFalse(RedfishJson.Attributes in query_result.data,
                         f"Result should filter out attribute {RedfishJson.Attributes} key")

        self.assertFalse('AttributeRegistry' in query_result.data,
                         "Result should filter out AttributeRegistry")

        self.assertFalse(RedfishJson.Data_id in query_result.data,
                         f"Failed to fetch mandatory {RedfishJson.Data_id} key")

        self.assertFalse("RandomKey" in query_result.data,
                         f"Failed to fetch mandatory {RedfishJson.Data_id} key")

        self.assertTrue("SystemInfo.1.BootTime" in query_result.data,
                        "Failed to retrieve key SystemInfo.1.BootTime key")
