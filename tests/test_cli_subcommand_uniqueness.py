"""CLI subcommand-name uniqueness guard.

Two commands that register the same argparse subcommand name are a latent
bug: on Python 3.10 the second registration silently clobbers the first
(one command becomes unreachable and dispatch is import-order dependent),
and on Python 3.11+ building the parser raises ``argparse.ArgumentError:
conflicting subparser``. That version-dependent split let a real duplicate
(`compute-query` claimed by both ComputeQuery/query and ComputeUpdate/update)
sit on main while the 3.10 CI leg stayed green.

These tests detect the collision directly from the command registry so it is
caught on every interpreter, not only 3.11+.

Author Mus <spyroot@gmail.com>
"""
import argparse

import redfish_ctl  # noqa: F401  — populates the registry with the wired commands
import redfish_ctl.compute.cmd_update  # noqa: F401  — force-load the un-wired update cmd
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.redfish_main import create_cmd_tree


def _registered_subcommand_names():
    """Map each CLI subcommand name to the registry entries that claim it."""
    registry = IDracManager().get_registry()
    names = {}
    for type_key in registry:
        for sub_key in registry[type_key]:
            cls = registry[type_key][sub_key]
            if hasattr(cls, "register_subcommand"):
                _, cmd_name, _ = cls.register_subcommand(cls)
                names.setdefault(cmd_name, []).append(f"{type_key}/{sub_key}")
    return names


def test_no_two_commands_share_a_cli_subcommand_name():
    """Every registered command must own a unique CLI subcommand name."""
    names = _registered_subcommand_names()
    duplicates = {name: owners for name, owners in names.items() if len(owners) > 1}
    assert not duplicates, f"duplicate CLI subcommand names: {duplicates}"


def test_compute_query_and_compute_update_are_distinct():
    """The compute read and update commands keep separate subcommand names."""
    names = _registered_subcommand_names()
    assert "compute-query" in names
    assert "compute-update" in names
    assert names["compute-query"] != names["compute-update"]


def test_full_parser_builds_without_conflict():
    """create_cmd_tree assembles every subparser (raises on 3.11+ if duplicated)."""
    parser = argparse.ArgumentParser(prog="redfish_ctl")
    mapping = create_cmd_tree(parser)
    # A well-formed tree maps each unique subcommand name to exactly one command.
    assert "compute-update" in mapping
    assert len(mapping) == len(set(mapping))
