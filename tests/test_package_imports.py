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
    from redfish_ctl.redfish_manager_base import RedfishManagerBase

    assert issubclass(BiosChangeSettings, RedfishManagerBase)


@pytest.mark.parametrize("old", ["redfish_ctl.idrac_manager", "redfish_ctl.idrac_shared"])
def test_old_idrac_module_names_no_longer_resolve(old):
    """The iDRAC-named base modules were hard-renamed to neutral names with no
    aliases (idrac_manager -> redfish_manager_base, idrac_shared ->
    redfish_manager_shared). The old import paths must no longer resolve, guarding
    against a reintroduction of the pre-rename names."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(old)
