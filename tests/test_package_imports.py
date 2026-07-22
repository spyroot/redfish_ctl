"""Offline smoke test: every idrac_ctl module imports cleanly.

This guards the import cleanup in bios/cmd_change_bios.py (duplicate and unused
imports removed) and, more generally, catches any command module that breaks at
import time. It is pure import-time work, so it needs no iDRAC.

Author Mus spyroot@gmail.com
"""
import importlib
import pkgutil

import pytest

import redfish_ctl


def _iter_module_names():
    for mod in pkgutil.walk_packages(redfish_ctl.__path__, prefix="redfish_ctl."):
        yield mod.name


@pytest.mark.parametrize("module_name", list(_iter_module_names()))
def test_module_imports(module_name: str):
    """Each submodule imports without raising."""
    importlib.import_module(module_name)


def test_bios_change_command_is_registered():
    """The de-duplicated bios change module still exposes its command class."""
    from redfish_ctl.bios.cmd_change_bios import BiosChangeSettings
    from redfish_ctl.idrac_manager import IDracManager

    assert issubclass(BiosChangeSettings, IDracManager)


@pytest.mark.parametrize(
    "removed",
    [
        "redfish_ctl.redfish_manager_base",
        "redfish_ctl.redfish_manager_shared",
    ],
)
def test_removed_neutral_base_names_no_longer_resolve(removed):
    """The Dell base-layer modules use vendor-honest names again: idrac_manager.py
    and idrac_shared.py. The transient neutral names redfish_manager_base and
    redfish_manager_shared were removed with no alias, so their import paths must no
    longer resolve -- guarding against reintroducing the inverted naming where the
    Dell child class masqueraded as the generic base. redfish_task_state.py exists
    again as the DMTF-generic TaskState module, so it is intentionally not listed."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(removed)
