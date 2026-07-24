"""CI enforcement of the gate registry and meta-gate (tools/gate_meta.py, gates/manifest.yaml)."""
import json
from pathlib import Path

from tools import gate_meta

REPO_ROOT = Path(__file__).resolve().parents[2]


def _profile_enum(node):
    """Find the gate-profile enum in the JSON schema, wherever it is nested.

    The schema's shape is not this test's business — only that a single string enum constrains a
    gate's profile. Searching for it keeps the allowed set in one place instead of copying it here.

    :param node: any node of the parsed JSON schema.
    :return: the list of allowed profile names, or an empty list when no such enum exists.
    """
    if isinstance(node, dict):
        if node.get("type") == "string" and isinstance(node.get("enum"), list) and "merge" in node["enum"]:
            return node["enum"]
        for value in node.values():
            found = _profile_enum(value)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _profile_enum(value)
            if found:
                return found
    return []


def test_meta_gate_passes():
    """gates/manifest.yaml and the pipeline are internally consistent (the meta-gate is green).

    This is the ``repo.meta`` gate run inside the offline suite, so a registry
    that references a missing/non-executable command, omits a mandatory ID, or
    (once they exist) mis-wires a GitLab job or a module, fails the build here.
    """
    ok, failures, _skipped = gate_meta.run()
    assert ok, "meta-gate failures:\n" + "\n".join(failures)


def test_registry_lists_every_mandatory_id_with_a_command():
    """Each mandatory gate ID is registered and carries an executable-looking command."""
    registry = gate_meta._load_registry()
    by_id = {g.get("id"): g for g in registry["gates"]}
    for mandatory in registry.get("mandatory_ids", []):
        assert mandatory in by_id, f"mandatory id {mandatory} is not registered"
        assert by_id[mandatory].get("command"), f"{mandatory} has no command"


def test_every_gate_declares_profile_and_mutates():
    """Every gate carries a schema-valid profile and an explicit mutation classification.

    The allowed set is read from schemas/gates.schema.json rather than repeated here. Hardcoding it
    made this test a fourth copy of the profile list — alongside check.sh, run.sh and the schema — and
    adding the repository-export profile broke it while the other three were already updated.
    """
    schema = json.loads((REPO_ROOT / "schemas" / "gates.schema.json").read_text(encoding="utf-8"))
    allowed = set(_profile_enum(schema))
    assert allowed, "the schema declares no profile enum; nothing constrains a gate's profile"

    registry = gate_meta._load_registry()
    for gate in registry["gates"]:
        assert gate.get("profile") in allowed, gate
        assert isinstance(gate.get("mutates"), bool), f"{gate.get('id')} lacks a bool 'mutates'"
