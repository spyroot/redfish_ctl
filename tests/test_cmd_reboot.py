import logging
import os
from unittest import TestCase

import pytest

from redfish_ctl.redfish_manager_base import RedfishManagerBase

logging.basicConfig()
log = logging.getLogger("LOG")
"/var/www/html/ph4-rt-refresh_adj_offline_testnf_os4_flex21.iso"


# Integration tests: require a reachable iDRAC.
# Skipped automatically unless IDRAC_IP is set (see tests/conftest.py).
pytestmark = pytest.mark.live

class TestReboot(TestCase):
    """
     Test reboot cmd
    """
    redfish_api = None

    @classmethod
    def setUpClass(cls) -> RedfishManagerBase:
        redfish_api = RedfishManagerBase(
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
