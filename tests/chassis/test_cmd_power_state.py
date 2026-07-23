import logging
import os
import time
from unittest import TestCase

import pytest

from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType

logging.basicConfig()
log = logging.getLogger("LOG")


# Integration tests: require a reachable iDRAC.
# Skipped automatically unless IDRAC_IP is set (see tests/conftest.py).
pytestmark = pytest.mark.live

class TestPowerState(TestCase):
    """
    Test chassis power state change.
    """
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

    def test_basic_query_power_state(self):
        """
        :return:
        """
        manager = self.setUpClass()
        self.assertTrue(manager.power_state is not manager.power_state.Unknown)
        # power_state = manager.power_state
        # logging.warning(power_state)

    def test_basic_power_on_off(self):
        """test basic query
        :return:
        """
        manager = self.setUpClass()
        # we, if power state is off do power on other power off
        if manager.power_state == manager.power_state.Off:
            _ = manager.sync_invoke(
                ApiRequestType.ChassisReset, "reboot",
                reset_type=manager.power_state.On.value
            )
            time.sleep(2)
            self.assertTrue(manager.power_state == manager.power_state.On)
        elif manager.power_state == manager.power_state.On:
            _ = manager.sync_invoke(
                ApiRequestType.ChassisReset, "reboot",
                reset_type=manager.power_state.Off.value
            )
            self.assertTrue(
                manager.power_state == manager.power_state.On
            )
            time.sleep(2)
