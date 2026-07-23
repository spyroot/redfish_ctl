"""iDRAC IDracManager test suite

Author Mus spyroot@gmail.com
"""
import logging
import os
from unittest import TestCase

import pytest

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.idrac_manager import CommandResult, IDracManager
from redfish_ctl.idrac_shared import ApiRequestType

img_location = "http://10.241.7.99/ph4-rt-refresh_adj_offline_testnf_os4_flex21.iso"

logging.basicConfig()
log = logging.getLogger("LOG")


# Integration tests: require a reachable iDRAC.
# Skipped automatically unless IDRAC_IP is set (see tests/conftest.py).
pytestmark = pytest.mark.live

class TestVirtualMedia(TestCase):
    redfish_api = None

    @classmethod
    def setUpClass(cls) -> IDracManager:
        redfish_api = IDracManager(
            idrac_ip=os.environ.get('IDRAC_IP', ''),
            idrac_username=os.environ.get('IDRAC_USERNAME', 'root'),
            idrac_password=os.environ.get('IDRAC_PASSWORD', ''),
            insecure=False,
            is_debug=False)
        return redfish_api

    def setUp(self) -> None:
        self.assertTrue(
            len(os.environ.get('IDRAC_IP', '')) > 0, "IDRAC_IP is none")
        self.assertTrue(
            len(os.environ.get('IDRAC_USERNAME', '')) > 0, "IDRAC_USERNAME is none")
        self.assertTrue(
            len(os.environ.get('IDRAC_PASSWORD', '')) > 0, "IDRAC_PASSWORD is none")

    def test_base_fetch_media(self):
        """Base test query for all media
        :return:
        """
        manager = self.setUpClass()
        query_result = manager.sync_invoke(
            ApiRequestType.VirtualMediaGet, "virtual_disk_query")
        self.assertIsInstance(query_result, CommandResult)
        self.assertIsInstance(query_result.data, dict)
        data = query_result.data
        self.assertTrue("Members" in data,
                        "member key must in respond")

    def test_base_fetch_media_device(self):
        """Base test fetch device by device id
        :return:
        """
        manager = self.setUpClass()
        query_result = manager.sync_invoke(
            ApiRequestType.VirtualMediaGet, "virtual_disk_query",
            device_id="1",
        )

        self.assertIsInstance(query_result, CommandResult)
        self.assertIsInstance(query_result.data, dict)
        data = query_result.data
        self.assertTrue("Inserted" in data,
                        "member key must in respond")
        self.assertTrue("ConnectedVia" in data,
                        "member key must in respond")
        self.assertTrue("Name" in data,
                        "member key must in respond")

    def test_base_fetch_media_device_not_found(self):
        """Base test for wrong dev id
        :return:
        """
        manager = self.setUpClass()
        query_result = manager.sync_invoke(
            ApiRequestType.VirtualMediaGet,
            "virtual_disk_query",
            device_id="10",
        )

        self.assertIsInstance(query_result, CommandResult)
        self.assertIsInstance(query_result.data, dict)
        data = query_result.data
        self.assertTrue("Status" in data,
                        "member key must in respond")

    def test_base_fetch_media_empty(self):
        """Base test for empty device list
        :return:
        """
        manager = self.setUpClass()
        query_result = manager.sync_invoke(
            ApiRequestType.VirtualMediaGet,
            "virtual_disk_query",
            device_id="",
        )

        self.assertIsInstance(query_result, CommandResult)
        self.assertIsInstance(query_result.data, dict)
        data = query_result.data
        self.assertTrue("Members" in data,
                        "member key must in respond")

    def test_base_insert_media(self):
        """Base test attach media no id must raise
        :return:
        """
        manager = self.setUpClass()

        self.assertRaises(InvalidArgument, manager.sync_invoke,
                          ApiRequestType.VirtualMediaInsert,
                          "virtual_disk_insert",
                          uri_path=img_location)

    def test_base_insert_media_no_args(self):
        """Base test attach media no device id must raise InvalidArgument
        :return:
        """
        manager = self.setUpClass()
        self.assertRaises(
            InvalidArgument, manager.sync_invoke,
            ApiRequestType.VirtualMediaInsert,
            "virtual_disk_insert"
        )

    def test_base_eject_and_insert(self):
        """Base test attach media no id must raise
        :return:
        """
        manager = self.setUpClass()

        cmd_resp = manager.sync_invoke(
            ApiRequestType.VirtualMediaInsert,
            "virtual_disk_insert",
            uri_path=img_location,
            device_id="1", do_eject=True
        )

        self.assertIsInstance(cmd_resp, CommandResult)
        self.assertIsInstance(cmd_resp.data, dict)
        data = cmd_resp.data
        self.assertTrue(
            "Status" in data,
            "Status key must in respond"
        )

    def test_base_eject(self):
        """Base test attach media no id must raise
        :return:
        """
        manager = self.setUpClass()

        cmd_resp = manager.sync_invoke(
            ApiRequestType.VirtualMediaEject,
            "virtual_disk_eject",
            device_id="1",
            do_strict=False)

        self.assertIsInstance(cmd_resp, CommandResult)
        self.assertIsInstance(cmd_resp.data, dict)
        data = cmd_resp.data
        self.assertTrue("Status" in data,
                        "Status key must in respond")
