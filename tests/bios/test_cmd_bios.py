"""This a unit test for query bios in redfish/idrac
endpoint.

Test all cmd options.  (Filter / Saving etc)
Filtering from command line, single or list of bios attributes
Filtering from query json file etc.

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
import pathlib
from json import JSONDecodeError
from unittest import TestCase

import pytest

import redfish_ctl
from redfish_ctl import save_if_needed
from redfish_ctl.idrac_manager import CommandResult, IDracManager
from redfish_ctl.idrac_shared import ApiRequestType

logging.basicConfig()
log = logging.getLogger("LOG")


# Integration tests: require a reachable iDRAC.
# Skipped automatically unless IDRAC_IP is set (see tests/conftest.py).
pytestmark = pytest.mark.live

class TestBios(TestCase):
    redfish_api = None

    @classmethod
    def setUpClass(cls) -> IDracManager:
        redfish_api = IDracManager(
            idrac_ip=os.environ.get('IDRAC_IP', ''),
            idrac_username=os.environ.get('IDRAC_USERNAME', 'root'),
            idrac_password=os.environ.get('IDRAC_PASSWORD', ''),
            insecure=True,
            is_debug=False)
        return redfish_api

    def setUp(self) -> None:
        self.assertTrue(
            len(os.environ.get('IDRAC_IP', '')) > 0, "IDRAC_IP is none")
        self.assertTrue(
            len(os.environ.get('IDRAC_USERNAME', '')) > 0, "IDRAC_USERNAME is none")
        self.assertTrue(
            len(os.environ.get('IDRAC_PASSWORD', '')) > 0, "IDRAC_PASSWORD is none")

    def test_basic_query(self):
        """test basic bios query
        :return:
        """
        manager = self.setUpClass()
        query_result = manager.sync_invoke(
            ApiRequestType.BiosQuery, "bios_inventory")

        self.assertIsInstance(query_result, CommandResult)
        self.assertIsInstance(query_result.data, dict)
        try:
            json.dumps(query_result.data,
                       sort_keys=True,
                       indent=4)
        except JSONDecodeError as _:
            self.fail("raised exception")

    def test_save_bios_query(
            self, bios_filename="/tmp/bios_test01.json"):
        """Test query a bios and save to a file respond.
        :return:
        """
        manager = self.setUpClass()
        query_result = manager.sync_invoke(
            ApiRequestType.BiosQuery, "bios_inventory",
            filename=bios_filename)

        self.assertIsInstance(query_result, CommandResult)
        self.assertIsInstance(query_result.data, dict)
        try:
            _ = json.dumps(query_result.data,
                           sort_keys=True,
                           indent=4)
        except JSONDecodeError as _:
            self.fail("raised exception")

        generated_file = pathlib.Path(bios_filename)
        self.assertTrue(generated_file.exists(),
                        "cmd must save a file")

        json_file = redfish_ctl.from_json_spec(bios_filename)
        try:
            _ = json.dumps(json_file, sort_keys=True)
        except JSONDecodeError as _:
            self.fail("raised exception")

        generated_file.unlink()

    def test_save_bios_query_filter_single(self):
        """Test query and filter single bios attribute
        :return:
        """
        manager = self.setUpClass()
        query_result = manager.sync_invoke(
            ApiRequestType.BiosQuery, "bios_inventory", attr_filter="ProcCStates")

        self.assertIsInstance(query_result, CommandResult)
        self.assertIsInstance(query_result.data, dict)
        try:
            _ = json.dumps(query_result.data, sort_keys=True, indent=4)
        except JSONDecodeError as _:
            self.fail("raised exception")

        query_data = query_result.data
        self.assertIsInstance(
            query_data, dict, "a query must be a dictionary"
        )
        self.assertTrue(
            "ProcCStates" in query_data, "query must contain filtered key"
        )
        self.assertTrue(
            len(query_data) == 1, "query must contain only single element"
        )

    def test_save_bios_query_filter_list(self):
        """Test query based on list of bios attributes.
        :return:
        """
        manager = self.setUpClass()
        query_result = manager.sync_invoke(
            ApiRequestType.BiosQuery, "bios_inventory",
            attr_filter="ProcCStates,SysMemSize")

        self.assertIsInstance(query_result, CommandResult)
        self.assertIsInstance(query_result.data, dict)
        try:
            _ = json.dumps(query_result.data, sort_keys=True, indent=4)
        except JSONDecodeError as _:
            self.fail("raised exception")

        query_data = query_result.data
        self.assertTrue("ProcCStates" in query_data, "query must contain filtered key")
        self.assertTrue("SysMemSize" in query_data, "query must contain filtered key")

    def test_save_bios_query_filter_and_save(
            self, filename="/tmp/bios_filter_save.json"):
        """test basic bios query on list of keys
        and save result to a file.
        :return:
        """
        manager = self.setUpClass()
        query_result = manager.sync_invoke(
            ApiRequestType.BiosQuery, "bios_inventory",
            attr_filter="ProcCStates,SysMemSize", filename=filename)

        self.assertIsInstance(query_result, CommandResult)
        self.assertIsInstance(query_result.data, dict)
        try:
            _ = json.dumps(query_result.data, sort_keys=True, indent=4)
        except JSONDecodeError as _:
            self.fail("raised exception")

        query_data = query_result.data
        self.assertTrue(
            "ProcCStates" in query_data, "query must contain filtered key"
        )
        self.assertTrue(
            "SysMemSize" in query_data, "query must contain filtered key"
        )

        json_file = redfish_ctl.from_json_spec(filename)
        self.assertIsInstance(json_file, dict)
        self.assertTrue(
            "ProcCStates" in json_file, "query must contain filtered key"
        )
        self.assertTrue(
            "SysMemSize" in json_file, "query must contain filtered key"
        )
        self.assertTrue(
            len(json_file) == 2, "file must contain only two keys"
        )

    def test_save_bios_query_from_file(
            self,
            query_file_name="/tmp/bios_query.json"):
        """test basic bios query on list of keys
        and save result to a file.
        :return:
        """
        manager = self.setUpClass()
        # key saved as query in json file
        key_list = ["ProcCStates,SysMemSize"]
        # save query to a file and use to , query bios
        save_if_needed(query_file_name, key_list)

        generated_file = pathlib.Path(query_file_name)
        self.assertTrue(
            generated_file.exists(),
            "generated query must exists"
        )

        query_result = manager.sync_invoke(
            ApiRequestType.BiosQuery, "bios_inventory",
            attr_filter="ProcCStates,SysMemSize",
            attr_filter_file=query_file_name)

        self.assertIsInstance(query_result, CommandResult)
        self.assertIsInstance(query_result.data, dict)
        try:
            _ = json.dumps(query_result.data, sort_keys=True, indent=4)
        except JSONDecodeError as _:
            self.fail("raised exception")

        query_data = query_result.data
        self.assertTrue(
            "ProcCStates" in query_data, "query must contain filtered key"
        )
        self.assertTrue(
            "SysMemSize" in query_data, "query must contain filtered key"
        )

        generated_file.unlink()
