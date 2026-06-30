"""Dual-mode tests for IDracManager power-state reads."""

from idrac_ctl.idrac_shared import PowerState


def test_power_state_reads_chassis_power_state(redfish_api):
    """power_state maps the chassis PowerState field to the enum value."""
    assert redfish_api.power_state == PowerState.On
