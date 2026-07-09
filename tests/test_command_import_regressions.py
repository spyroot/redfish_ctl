"""Regression tests for two commands that referenced names they never imported,
which raised ``NameError`` at runtime.

- ``cmd_convert_to_raid`` calls ``find_ids()`` (from ``cmd_utils``).
- ``cmd_initilize`` raises ``UnsupportedAction`` (from ``cmd_exceptions``).

These guard the imports so the commands can actually execute instead of blowing
up the first time the relevant branch is reached.
"""


def test_convert_to_raid_imports_find_ids():
    """cmd_convert_to_raid uses find_ids() — it must be importable (was NameError)."""
    import redfish_ctl.storage.cmd_convert_to_raid as mod

    assert callable(mod.find_ids)


def test_volume_init_imports_unsupported_action():
    """cmd_initilize raises UnsupportedAction — it must be importable (was NameError)."""
    import redfish_ctl.volumes.cmd_initilize as mod

    assert isinstance(mod.UnsupportedAction, type)
