"""Destructiveness policy for Redfish actions.

The single source of truth for how risky each Redfish action is, keyed by its
full ``#Type.Action`` name. ``invoke_action`` (redfish_manager_base.py) consults this to
decide whether an action runs freely, runs with a one-line notice, defaults to a
dry-run unless ``--confirm`` is given, or additionally needs an explicit
"I understand this is irreversible" token.

Fail-safe by construction: an action not in the table is treated as DESTRUCTIVE,
so a newly exposed (unclassified) action can never POST without an explicit
confirm. This module is product-neutral — it names standard DMTF + NVIDIA OEM
action types only and imports nothing from the Redfish manager layer.

Author Mus spyroot@gmail.com
"""
from enum import Enum


class Destructiveness(Enum):
    """How disruptive running a Redfish action is.

    READ_ONLY    POST is just a transport for a query (e.g. fetch signed
                 measurements); no state change. Runs freely.
    REVERSIBLE   Changes state but is recoverable (insert media, power-tuning
                 profile, test event). Runs, with a one-line notice.
    DESTRUCTIVE  Disrupts service or rewrites config (any reset, BIOS reset,
                 replace certificate). Defaults to a dry-run; needs ``--confirm``.
    IRREVERSIBLE Causes data loss or a one-way security change (secure erase,
                 key revocation, factory reset). Needs ``--confirm`` AND the
                 explicit irreversible token.
    """
    READ_ONLY = "read_only"
    REVERSIBLE = "reversible"
    DESTRUCTIVE = "destructive"
    IRREVERSIBLE = "irreversible"


# Keyed by the full Redfish action type "#Type.Action" (as discover_redfish_actions
# reports it in RedfishAction.full_redfish_name). Covers the 25 action types the
# GB300 NVL exposes plus a couple of standard siblings.
ACTION_POLICY = {
    # read-only: a signed-measurement fetch carried over POST
    "#ComponentIntegrity.SPDMGetSignedMeasurements": Destructiveness.READ_ONLY,
    "#ComponentIntegrity.TPMGetSignedMeasurements": Destructiveness.READ_ONLY,
    "#DellOSDeploymentService.GetDriverPackInfo": Destructiveness.READ_ONLY,

    # reversible: state changes that can be undone
    "#EventService.SubmitTestEvent": Destructiveness.REVERSIBLE,
    "#HpeDirectoryTest.StartTest": Destructiveness.REVERSIBLE,
    "#HpeDirectoryTest.StopTest": Destructiveness.REVERSIBLE,
    "#HpeiLOSnmpService.SendSNMPTestAlert": Destructiveness.REVERSIBLE,
    "#HpeiLOManagerNetworkService.SendTestAlertMail": Destructiveness.REVERSIBLE,
    "#HpeiLOManagerNetworkService.SendTestSyslog": Destructiveness.REVERSIBLE,
    "#VirtualMedia.InsertMedia": Destructiveness.REVERSIBLE,
    "#VirtualMedia.EjectMedia": Destructiveness.REVERSIBLE,
    "#CertificateService.GenerateCSR": Destructiveness.REVERSIBLE,
    "#LogService.CollectDiagnosticData": Destructiveness.REVERSIBLE,
    "#NvidiaPowerSmoothing.ActivatePresetProfile": Destructiveness.REVERSIBLE,
    "#NvidiaPowerSmoothing.ApplyAdminOverrides": Destructiveness.REVERSIBLE,
    "#NvidiaWorkloadPower.EnableProfiles": Destructiveness.REVERSIBLE,
    "#NvidiaWorkloadPower.DisableProfiles": Destructiveness.REVERSIBLE,
    "#NvidiaDebugToken.GenerateToken": Destructiveness.REVERSIBLE,
    "#NvidiaDebugToken.DisableToken": Destructiveness.REVERSIBLE,

    # destructive: service disruption / config rewrite — dry-run unless --confirm
    "#ComputerSystem.Reset": Destructiveness.DESTRUCTIVE,
    "#Manager.Reset": Destructiveness.DESTRUCTIVE,
    "#Chassis.Reset": Destructiveness.DESTRUCTIVE,
    "#NetworkAdapter.Reset": Destructiveness.DESTRUCTIVE,
    "#Control.ResetToDefaults": Destructiveness.DESTRUCTIVE,
    "#Bios.ResetBios": Destructiveness.DESTRUCTIVE,
    "#Bios.ChangePassword": Destructiveness.DESTRUCTIVE,
    "#LicenseService.Install": Destructiveness.DESTRUCTIVE,
    # ClearLog erases log entries (unrecoverable), but it neither disrupts the
    # host/BMC nor makes a one-way security change, so it sits at DESTRUCTIVE
    # (--confirm) rather than IRREVERSIBLE (the extra token is reserved for
    # secure-erase / RoT-key / factory-reset class actions).
    "#LogService.ClearLog": Destructiveness.DESTRUCTIVE,
    "#TelemetryService.ClearMetricReports": Destructiveness.DESTRUCTIVE,
    "#Volume.CheckConsistency": Destructiveness.DESTRUCTIVE,
    "#CertificateService.ReplaceCertificate": Destructiveness.DESTRUCTIVE,
    "#SecureBootDatabase.ResetKeys": Destructiveness.DESTRUCTIVE,
    "#NvidiaDebugToken.InstallToken": Destructiveness.DESTRUCTIVE,
    "#UpdateService.SimpleUpdate": Destructiveness.DESTRUCTIVE,
    "#UpdateService.StartUpdate": Destructiveness.DESTRUCTIVE,

    # irreversible: data loss or one-way security change — needs the extra token
    "#Drive.SecureErase": Destructiveness.IRREVERSIBLE,
    "#Manager.ResetToDefaults": Destructiveness.IRREVERSIBLE,
    "#NvidiaRoTProtectedComponent.RevokeKeys": Destructiveness.IRREVERSIBLE,
    "#NvidiaRoTProtectedComponent.UpdateMinimumSecurityVersion": Destructiveness.IRREVERSIBLE,
}

# An unclassified action is treated as DESTRUCTIVE: it can never POST without an
# explicit --confirm, so a newly exposed action fails safe rather than firing.
DEFAULT_LEVEL = Destructiveness.DESTRUCTIVE


def classify(full_action_type):
    """Return the Destructiveness of a Redfish action by its ``#Type.Action`` name.

    An empty/None name or any action not in ACTION_POLICY falls back to
    DEFAULT_LEVEL (DESTRUCTIVE) so the safe path is the default.

    :param full_action_type: full ``#Type.Action`` name to look up.
    :return: the Destructiveness level, or DEFAULT_LEVEL (DESTRUCTIVE) when the
        name is empty or absent from ACTION_POLICY.
    """
    if not full_action_type:
        return DEFAULT_LEVEL
    return ACTION_POLICY.get(full_action_type, DEFAULT_LEVEL)
