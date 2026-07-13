"""This a unit test for query bios registry. filtering
 read only , none read only , subset etc

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
import pathlib
from json import JSONDecodeError
from unittest import TestCase

import pytest

import redfish_ctl
from redfish_ctl.base_manager import CommandBase, CommandResult
from redfish_ctl.command_shared import ApiRequestType

logging.basicConfig()
log = logging.getLogger("LOG")


# Integration tests: require a reachable iDRAC.
# Skipped automatically unless REDFISH_IP is set (see tests/conftest.py).
pytestmark = pytest.mark.live

class TestBiosRegistry(TestCase):
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
        self.assertTrue(
            len(os.environ.get('REDFISH_PASSWORD', '')) > 0, "REDFISH_PASSWORD is none")

    def test_basic_bios_registry_query(self):
        """test basic query
        :return:
        """
        manager = self.setUpClass()
        query_result = manager.sync_invoke(
            ApiRequestType.BiosRegistry, "bios_registry")

        self.assertIsInstance(query_result, CommandResult)
        self.assertIsInstance(query_result.data, list)
        try:
            json.dumps(query_result.data, sort_keys=True, indent=4)
        except JSONDecodeError as _:
            self.fail("raised exception")

    def test_save_bios_registry_query(self):
        """test basic query
        :return:
        """
        manager = self.setUpClass()
        query_result = manager.sync_invoke(
            ApiRequestType.BiosRegistry,
            "bios_registry",
            filename="/tmp/bios_registry.json"
        )

        self.assertIsInstance(query_result, CommandResult)
        self.assertIsInstance(query_result.data, list)
        try:
            _ = json.dumps(query_result.data, sort_keys=True, indent=4)
        except JSONDecodeError as _:
            self.fail("raised exception")

        generated_file = pathlib.Path("/tmp/bios_registry.json")
        self.assertTrue(generated_file.exists(),
                        "cmd must save a file")

        json_file = redfish_ctl.from_json_spec("/tmp/bios_registry.json")
        try:
            _ = json.dumps(json_file, sort_keys=True)
        except JSONDecodeError as _:
            self.fail("raised exception")

        generated_file.unlink()

    def test_save_bios_save_no_read_only(
            self, filename="/tmp/bios_registry_no_read_only.json"):
        """test basic query
        :return:
        """
        manager = self.setUpClass()
        query_result = manager.sync_invoke(
            ApiRequestType.BiosRegistry, "bios_registry",
            filename=filename,
            no_read_only=True)

        self.assertIsInstance(query_result, CommandResult)
        self.assertIsInstance(query_result.data, list)
        try:
            _ = json.dumps(query_result.data, sort_keys=True, indent=4)
        except JSONDecodeError as _:
            self.fail("raised exception")

        generated_file = pathlib.Path(filename)
        self.assertTrue(
            generated_file.exists(), "cmd must save a file")

        json_file = redfish_ctl.from_json_spec(
            filename)
        try:
            _ = json.dumps(json_file, sort_keys=True)
        except JSONDecodeError as _:
            self.fail("raised exception")

        for e in json_file:
            self.assertEqual(e["ReadOnly"], False,
                             "read only must be false")
        generated_file.unlink()
