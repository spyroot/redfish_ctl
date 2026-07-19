"""CI enforcement of the gate registry and meta-gate (tools/gate_meta.py, gates/manifest.yaml)."""
from tools import gate_meta


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
    """Every gate carries a profile and an explicit mutation classification."""
    registry = gate_meta._load_registry()
    for gate in registry["gates"]:
        assert gate.get("profile") in {"merge", "integration", "deploy"}, gate
        assert isinstance(gate.get("mutates"), bool), f"{gate.get('id')} lacks a bool 'mutates'"
