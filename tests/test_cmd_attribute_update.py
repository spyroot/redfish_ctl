"""This a unit test for updating idrac or redfish endpoint
attribute.

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

from redfish_ctl import save_if_needed
from redfish_ctl.base_manager import CommandBase, CommandResult
from redfish_ctl.command_shared import ApiRequestType
from redfish_ctl.redfish_exceptions import RedfishException
from redfish_ctl.redfish_shared import RedfishJson

logging.basicConfig()
log = logging.getLogger("LOG")


# Integration tests: require a reachable iDRAC.
# Skipped automatically unless REDFISH_IP is set (see tests/conftest.py).
pytestmark = pytest.mark.live

class TestUpdateAttribute(TestCase):
    """
    Attribute update cmd unit test.
    """
    redfish_api = None

    @classmethod
    def setUpClass(cls) -> CommandBase:
        """Setup required envs.
        :return:
        """
        redfish_api = CommandBase(idrac_ip=os.environ.get('REDFISH_IP', ''),
                                   idrac_username=os.environ.get('REDFISH_USERNAME', 'root'),
                                   idrac_password=os.environ.get('REDFISH_PASSWORD', ''),
                                   insecure=True,
                                   is_debug=False)
        return redfish_api

    def setUp(self) -> None:
        """Check envs
        :return:
        """
        self.assertTrue(len(os.environ.get('REDFISH_IP', '')) > 0, "REDFISH_IP is none")
        self.assertTrue(len(os.environ.get('REDFISH_USERNAME', '')) > 0, "REDFISH_USERNAME is none")
        self.assertTrue(len(os.environ.get('REDFISH_PASSWORD', '')) > 0, "REDFISH_PASSWORD is none")

    def test_basic_attribute_query(self):
        """test basic query before we do any update
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

    def test_basic_update(self, default_value="redfish_ctl"):
        """Test basic update, query sequence
        :return:
        """
        manager = self.setUpClass()
        # save query to a file and use to , query bios

        query_file_name = "/tmp/attribute_update.json"
        data_dict = {
            "Attributes": {
                "OwnerInfo.1.OwnerName": default_value
            }
        }

        save_if_needed(query_file_name, data_dict)

        update_cmd_resp = manager.sync_invoke(
            ApiRequestType.AttributesUpdate,
            "attribute_update", from_spec=query_file_name
        )
        self.assertIsInstance(update_cmd_resp, CommandResult)
        self.assertIsInstance(update_cmd_resp.data, dict)
        self.assertTrue(hasattr(CommandResult, "data"),
                        "respond must contain")
        self.assertTrue("Status" in update_cmd_resp.data,
                        "update attribute should return status")
        try:
            json.dumps(update_cmd_resp.data, sort_keys=True, indent=4)
        except TypeError as _:
            self.fail("raised exception")
        except JSONDecodeError as _:
            self.fail("raised exception")
        #
        # query for a same key
        query_result = manager.sync_invoke(
            ApiRequestType.AttributesQuery,
            "attribute_inventory",
            attr_filter="OwnerInfo.1.OwnerName"
        )

        self.assertIsInstance(query_result, CommandResult)
        self.assertIsInstance(query_result.data, dict)
        respond_data = query_result.data
        self.assertTrue(
            "OwnerInfo.1.OwnerName" in respond_data,
            "key must be in query respond"
        )

        new_value = respond_data["OwnerInfo.1.OwnerName"]
        self.assertTrue(
            default_value == new_value,
            "value must change after update"
        )

    def test_basic_update_none_existing(
            self, default_value="redfish_ctl"):
        """Test basic update, with wrong key.
        :return:
        """
        manager = self.setUpClass()
        # save query to a file and use to , query bios

        query_file_name = "/tmp/attribute_update.json"
        data_dict = {
            "Attributes": {
                "RandomValue": default_value
            }
        }
        # save to a file
        save_if_needed(query_file_name, data_dict)
        self.assertRaises(RedfishException,
                          manager.sync_invoke,
                          ApiRequestType.AttributesUpdate,
                          "attribute_update",
                          from_spec=query_file_name)
