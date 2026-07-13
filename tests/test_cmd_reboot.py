import logging
import os
from unittest import TestCase

import pytest

from redfish_ctl.base_manager import CommandBase

logging.basicConfig()
log = logging.getLogger("LOG")
"/var/www/html/ph4-rt-refresh_adj_offline_testnf_os4_flex21.iso"


# Integration tests: require a reachable iDRAC.
# Skipped automatically unless REDFISH_IP is set (see tests/conftest.py).
pytestmark = pytest.mark.live

class TestReboot(TestCase):
    """
     Test reboot cmd
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
        self.assertTrue(
            len(os.environ.get('REDFISH_PASSWORD', '')) > 0, "REDFISH_PASSWORD is none")

    def test_base_reboot(self):
        """test base reboot seq
        :return:
        """
        manager = self.setUpClass()
        cmd_resp = manager.reboot()
        log.warning(cmd_resp.data)

    def test_base_reboot_watch(self):
        """test base reboot and watch.
        :return:
        """
        manager = self.setUpClass()
        cmd_resp = manager.reboot(do_watch=True)
        log.warning(cmd_resp.data)
