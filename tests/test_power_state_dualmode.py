"""Dual-mode tests for CommandBase power-state reads."""

from redfish_ctl.command_shared import PowerState


def test_power_state_reads_chassis_power_state(redfish_api):
    """power_state maps the chassis PowerState field to the enum value."""
    assert redfish_api.power_state == PowerState.On
