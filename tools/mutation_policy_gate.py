"""Gate: destructive Redfish actions can never be classified below DESTRUCTIVE.

The round-trip gate (repo.live-mutation-roundtrip) proves reversible mutations
restore their value. This gate guards the other side: an action that powers a
host off, changes a password, replaces a certificate, resets a manager, or
erases data must stay at DESTRUCTIVE or IRREVERSIBLE in
``redfish_ctl.actions.action_policy.ACTION_POLICY``. A future edit that
downgrades one to REVERSIBLE or READ_ONLY would let it run through the
reversible path (or freely), defeating the confirm requirement — this gate
fails that edit at CI.

    python3 tools/mutation_policy_gate.py

Exit 0 when every invariant holds; exit 1 listing each violation.

Author Mus spyroot@gmail.com
"""
from __future__ import annotations

import sys

from redfish_ctl.actions.action_policy import (
    ACTION_POLICY,
    DEFAULT_LEVEL,
    Destructiveness,
)

# Ordered least→most disruptive, so "at least DESTRUCTIVE" is an index compare.
_ORDER = [
    Destructiveness.READ_ONLY,
    Destructiveness.REVERSIBLE,
    Destructiveness.DESTRUCTIVE,
    Destructiveness.IRREVERSIBLE,
]

# Any action whose type name contains one of these substrings MUST classify at
# DESTRUCTIVE or above. The names describe service-disrupting or config/security
# rewriting operations; none is ever safely reversible-or-below.
_DANGER_SUBSTRINGS = (
    "Reset",            # ComputerSystem/Manager/Chassis/NetworkAdapter/Control/Bios/SecureBoot
    "ChangePassword",   # credential change
    "ReplaceCertificate",  # BMC/service certificate replacement
    "SecureErase",      # drive data loss
    "RevokeKeys",       # RoT key revocation
    "ResetKeys",        # SecureBoot key reset
    "ClearLog",         # log erasure
    "ClearMetricReports",
    "SimpleUpdate",     # firmware
    "StartUpdate",
    "Install",          # license/firmware/debug-token install
    "CheckConsistency",
    "MinimumSecurityVersion",  # one-way security-version bump
)

# Named anchors that MUST be present and at least DESTRUCTIVE. These map to the
# operator's absolute hard-no families (power/reset, manager reset, cert change,
# password change); their absence is itself a regression, so presence is checked
# as well as level.
_REQUIRED_MIN = {
    "#ComputerSystem.Reset": Destructiveness.DESTRUCTIVE,      # power off / reboot / cycle
    "#Manager.Reset": Destructiveness.DESTRUCTIVE,             # BMC reset
    "#Manager.ResetToDefaults": Destructiveness.DESTRUCTIVE,   # BMC factory reset
    "#CertificateService.ReplaceCertificate": Destructiveness.DESTRUCTIVE,  # cert change
    "#Bios.ChangePassword": Destructiveness.DESTRUCTIVE,       # password change
}


def _at_least(level: Destructiveness, minimum: Destructiveness) -> bool:
    """Return True when ``level`` is as disruptive as ``minimum`` or more.

    :param level: the classification under test.
    :param minimum: the floor it must reach.
    :return: True when level ranks at or above minimum.
    """
    return _ORDER.index(level) >= _ORDER.index(minimum)


def check() -> list[str]:
    """Return every policy-invariant violation; empty list when clean.

    :return: human-readable violation strings, one per broken invariant.
    """
    bad: list[str] = []

    if DEFAULT_LEVEL is not Destructiveness.DESTRUCTIVE:
        bad.append(
            f"DEFAULT_LEVEL is {DEFAULT_LEVEL.value}, must be destructive "
            "(an unclassified action has to fail safe)")

    for name, level in ACTION_POLICY.items():
        if any(s in name for s in _DANGER_SUBSTRINGS):
            if not _at_least(level, Destructiveness.DESTRUCTIVE):
                bad.append(
                    f"{name} is {level.value}; a destructive-keyword action "
                    "must be destructive or irreversible")

    for name, minimum in _REQUIRED_MIN.items():
        if name not in ACTION_POLICY:
            bad.append(f"{name} missing from ACTION_POLICY (hard-no family)")
        elif not _at_least(ACTION_POLICY[name], minimum):
            bad.append(
                f"{name} is {ACTION_POLICY[name].value}, must be at least "
                f"{minimum.value}")

    return bad


def main() -> int:
    """Run the invariant check and report.

    :return: 0 when every invariant holds, 1 otherwise.
    """
    bad = check()
    for line in bad:
        print(f"mutation-policy: {line}")
    if bad:
        print(f"mutation-policy: {len(bad)} violation(s)")
        return 1
    print("mutation-policy: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
