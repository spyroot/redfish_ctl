"""Offline tests for the destructive-action policy gate.

The gate (tools/mutation_policy_gate.py) locks the invariant that no
service-disrupting or credential/security-rewriting action can be classified
below DESTRUCTIVE. These tests prove the real policy satisfies it and that the
gate actually catches a downgrade — a gate that always passes is worthless.

Author Mus spyroot@gmail.com
"""
import pytest

from redfish_ctl.actions.action_policy import Destructiveness
from tools import mutation_policy_gate as gate


def test_real_policy_is_clean():
    """The shipped ACTION_POLICY must satisfy every invariant."""
    assert gate.check() == []


def test_main_exit_zero_on_clean_policy():
    """main() returns 0 against the real policy."""
    assert gate.main() == 0


@pytest.mark.parametrize("name", [
    "#ComputerSystem.Reset",
    "#Manager.Reset",
    "#Manager.ResetToDefaults",
    "#CertificateService.ReplaceCertificate",
    "#Bios.ChangePassword",
])
def test_hard_no_family_downgrade_is_caught(monkeypatch, name):
    """Downgrading any hard-no anchor (power/reset/manager/cert/password) to
    REVERSIBLE must be reported — this is the regression the gate exists for."""
    from redfish_ctl.actions import action_policy
    patched = dict(action_policy.ACTION_POLICY)
    patched[name] = Destructiveness.REVERSIBLE
    monkeypatch.setattr(gate, "ACTION_POLICY", patched)
    bad = gate.check()
    assert any(name in line for line in bad), f"downgrade of {name} not caught"


def test_missing_hard_no_family_is_caught(monkeypatch):
    """Deleting a hard-no family from the policy is a violation — absence must
    not silently drop it to the (safe) default and pass."""
    from redfish_ctl.actions import action_policy
    patched = dict(action_policy.ACTION_POLICY)
    del patched["#CertificateService.ReplaceCertificate"]
    monkeypatch.setattr(gate, "ACTION_POLICY", patched)
    assert any("ReplaceCertificate" in line and "missing" in line
               for line in gate.check())


def test_default_level_downgrade_is_caught(monkeypatch):
    """If the fail-safe default is loosened from DESTRUCTIVE, the gate fails —
    an unclassified action must never fall through to a runnable level."""
    monkeypatch.setattr(gate, "DEFAULT_LEVEL", Destructiveness.REVERSIBLE)
    assert any("DEFAULT_LEVEL" in line for line in gate.check())


def test_new_keyworded_action_must_be_destructive(monkeypatch):
    """A newly added action whose name carries a destructive keyword (here a
    firmware SimpleUpdate) may not be introduced at REVERSIBLE."""
    from redfish_ctl.actions import action_policy
    patched = dict(action_policy.ACTION_POLICY)
    patched["#UpdateService.SimpleUpdate"] = Destructiveness.REVERSIBLE
    monkeypatch.setattr(gate, "ACTION_POLICY", patched)
    assert any("SimpleUpdate" in line for line in gate.check())
