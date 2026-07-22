"""Destructiveness policy for Redfish actions.

The single source of truth for how risky each Redfish action is, keyed by its
full ``#Type.Action`` name. ``invoke_action`` (idrac_manager.py) consults this to
decide whether an action runs freely, runs with a one-line notice, defaults to a
dry-run unless ``--confirm`` is given, or additionally needs an explicit
"I understand this is irreversible" token.

Fail-safe by construction: an action not in the table is treated as DESTRUCTIVE,
so a newly exposed (unclassified) action can never POST without an explicit
confirm. This module is product-neutral — it names standard DMTF plus known OEM
confirm. This module is product-neutral — it names standard DMTF and vendor OEM
action types and imports nothing from the Redfish manager layer.
confirm. This module is product-neutral — it names standard DMTF actions and
selected vendor OEM action types without importing the Redfish manager layer.
confirm. This module is product-neutral — it names standard DMTF and vendor OEM
action types and imports nothing from the Redfish manager layer.

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
# reports it in RedfishAction.full_redfish_name).
ACTION_POLICY = {
    # read-only: a signed-measurement fetch carried over POST
    "#ComponentIntegrity.SPDMGetSignedMeasurements": Destructiveness.READ_ONLY,
    "#ComponentIntegrity.TPMGetSignedMeasurements": Destructiveness.READ_ONLY,
    "#DellLCService.SupportAssistGetAutoCollectSchedule": Destructiveness.READ_ONLY,
    "#DellLCService.SupportAssistGetEULAStatus": Destructiveness.READ_ONLY,
    "#DelliDRACCardService.ExportCertificate": Destructiveness.READ_ONLY,
    "#DelliDRACCardService.ExportSSLCertificate": Destructiveness.READ_ONLY,
    "#DelliDRACCardService.FactoryIdentityExportCertificate": (
        Destructiveness.READ_ONLY
    ),
    "#DelliDRACCardService.VerifyHWProofOfPossession": Destructiveness.READ_ONLY,
    "#DellRaidService.CheckVDValues": Destructiveness.READ_ONLY,

    # reversible: state changes that can be undone
    "#EventService.SubmitTestEvent": Destructiveness.REVERSIBLE,
    "#DelliDRACCardService.TestSEKMServerConnection": Destructiveness.REVERSIBLE,
    "#HpeDirectoryTest.StartTest": Destructiveness.REVERSIBLE,
    "#HpeDirectoryTest.StopTest": Destructiveness.REVERSIBLE,
    "#HpeiLOSnmpService.SendSNMPTestAlert": Destructiveness.REVERSIBLE,
    "#HpeiLOManagerNetworkService.SendTestAlertMail": Destructiveness.REVERSIBLE,
    "#HpeiLOManagerNetworkService.SendTestSyslog": Destructiveness.REVERSIBLE,
    "#DellLCService.TestNetworkShare": Destructiveness.REVERSIBLE,
    "#DelliDRACCardService.SendTestEmailAlert": Destructiveness.REVERSIBLE,
    "#DelliDRACCardService.SendTestSNMPTrap": Destructiveness.REVERSIBLE,
    "#DelliDRACCardService.TestRsyslogServerConnection": Destructiveness.REVERSIBLE,
    "#TelemetryService.SubmitTestMetricReport": Destructiveness.REVERSIBLE,
    "#VirtualMedia.InsertMedia": Destructiveness.REVERSIBLE,
    "#VirtualMedia.EjectMedia": Destructiveness.REVERSIBLE,
    "#CertificateService.GenerateCSR": Destructiveness.REVERSIBLE,
    "#DelliDRACCardService.FactoryIdentityCertificateGenerateCSR": Destructiveness.REVERSIBLE,
    "#DelliDRACCardService.GenerateSEKMCSR": Destructiveness.REVERSIBLE,
    "#LogService.CollectDiagnosticData": Destructiveness.REVERSIBLE,
    "#NvidiaPowerSmoothing.ActivatePresetProfile": Destructiveness.REVERSIBLE,
    "#NvidiaPowerSmoothing.ApplyAdminOverrides": Destructiveness.REVERSIBLE,
    "#NvidiaWorkloadPower.EnableProfiles": Destructiveness.REVERSIBLE,
    "#NvidiaWorkloadPower.DisableProfiles": Destructiveness.REVERSIBLE,
    "#NvidiaDebugToken.GenerateToken": Destructiveness.REVERSIBLE,
    "#NvidiaDebugToken.DisableToken": Destructiveness.REVERSIBLE,
    (
        "#DellSwitchConnectionService.ServerPortConnectionRefresh"
    ): Destructiveness.REVERSIBLE,
    "#DellRaidService.BlinkTarget": Destructiveness.REVERSIBLE,
    "#DellRaidService.UnBlinkTarget": Destructiveness.REVERSIBLE,
    "#DellLCService.UpdateOSAppHealthData": Destructiveness.REVERSIBLE,
    "#DellRaidService.StartPatrolRead": Destructiveness.REVERSIBLE,
    "#DellRaidService.StopPatrolRead": Destructiveness.REVERSIBLE,
    "#DellLCService.SupportAssistClearAutoCollectSchedule": Destructiveness.REVERSIBLE,
    "#DellLCService.SupportAssistSetAutoCollectSchedule": Destructiveness.REVERSIBLE,

    # destructive: service disruption / config rewrite — dry-run unless --confirm
    "#ComputerSystem.Reset": Destructiveness.DESTRUCTIVE,
    "#Manager.Reset": Destructiveness.DESTRUCTIVE,
    "#Chassis.Reset": Destructiveness.DESTRUCTIVE,
    "#NetworkAdapter.Reset": Destructiveness.DESTRUCTIVE,
    "#Control.ResetToDefaults": Destructiveness.DESTRUCTIVE,
    "#Bios.ResetBios": Destructiveness.DESTRUCTIVE,
    "#Bios.ChangePassword": Destructiveness.DESTRUCTIVE,
    "#DellBIOSService.DeviceRecovery": Destructiveness.DESTRUCTIVE,
    "#DellPersistentStorageService.VFlashStateChange": Destructiveness.DESTRUCTIVE,
    "#DellLCService.ClearProvisioningServer": Destructiveness.DESTRUCTIVE,
    "#LicenseService.Install": Destructiveness.DESTRUCTIVE,
    "#HpeServerChassis.DisableMCTPOnServer": Destructiveness.DESTRUCTIVE,
    "#HpeiLOAccountService.ImportKerberosKeytab": Destructiveness.DESTRUCTIVE,
    "#DellLicenseManagementService.DeleteLicense": Destructiveness.DESTRUCTIVE,
    "#DellLicenseManagementService.ExportLicense": Destructiveness.DESTRUCTIVE,
    "#DellLicenseManagementService.ExportLicenseByDevice": Destructiveness.DESTRUCTIVE,
    "#DellLicenseManagementService.ExportLicenseByDeviceToNetworkShare": Destructiveness.DESTRUCTIVE,
    "#DellLicenseManagementService.ExportLicenseToNetworkShare": Destructiveness.DESTRUCTIVE,
    "#DellLicenseManagementService.ImportLicense": Destructiveness.DESTRUCTIVE,
    "#DellLicenseManagementService.ImportLicenseFromNetworkShare": Destructiveness.DESTRUCTIVE,
    "#DellRaidService.AssignSpare": Destructiveness.DESTRUCTIVE,
    "#DellRaidService.UnassignSpare": Destructiveness.DESTRUCTIVE,
    "#DellLCService.ExportCompleteLCLog": Destructiveness.DESTRUCTIVE,
    "#DellLCService.ExportFactoryConfiguration": Destructiveness.DESTRUCTIVE,
    "#DellLCService.ExportHWInventory": Destructiveness.DESTRUCTIVE,
    "#DellLCService.ExportLCLog": Destructiveness.DESTRUCTIVE,
    "#DellLCService.ExportSVGFile": Destructiveness.DESTRUCTIVE,
    "#DellLCService.ExportServerScreenShot": Destructiveness.DESTRUCTIVE,
    "#DellLCService.ExportTechSupportReport": Destructiveness.DESTRUCTIVE,
    "#DellLCService.ExportVideoLog": Destructiveness.DESTRUCTIVE,
    "#DellLCService.ExportePSADiagnosticsResult": Destructiveness.DESTRUCTIVE,
    "#DellSoftwareInstallationService.ClearUpdateSchedule": Destructiveness.DESTRUCTIVE,
    "#DellSoftwareInstallationService.SetUpdateSchedule": Destructiveness.DESTRUCTIVE,
    "#DellLCService.SupportAssistExportLastCollection": Destructiveness.DESTRUCTIVE,
    "#DellPersistentStorageService.AttachPartition": Destructiveness.DESTRUCTIVE,
    "#DellPersistentStorageService.CreatePartition": Destructiveness.DESTRUCTIVE,
    "#DellPersistentStorageService.CreatePartitionUsingImage": Destructiveness.DESTRUCTIVE,
    "#DellPersistentStorageService.DetachPartition": Destructiveness.DESTRUCTIVE,
    "#DellPersistentStorageService.ExportDataFromPartition": Destructiveness.DESTRUCTIVE,
    "#DellPersistentStorageService.ModifyPartition": Destructiveness.DESTRUCTIVE,
    "#DellRaidService.RenameVD": Destructiveness.DESTRUCTIVE,
    # ClearLog erases log entries (unrecoverable), but it neither disrupts the
    # host/BMC nor makes a one-way security change, so it sits at DESTRUCTIVE
    # (--confirm) rather than IRREVERSIBLE (the extra token is reserved for
    # secure-erase / RoT-key / factory-reset class actions).
    "#LogService.ClearLog": Destructiveness.DESTRUCTIVE,
    "#TelemetryService.ClearMetricReports": Destructiveness.DESTRUCTIVE,
    "#TelemetryService.ResetMetricReportDefinitionsToDefaults": Destructiveness.DESTRUCTIVE,
    "#DellJobService.SetupJobQueue": Destructiveness.DESTRUCTIVE,
    "#DellRaidService.SetAssetName": Destructiveness.DESTRUCTIVE,
    "#DellRaidService.SetBootVD": Destructiveness.DESTRUCTIVE,
    "#DellSystemManagementService.ShowErrorsOnLCD": Destructiveness.DESTRUCTIVE,
    "#DellLCService.ReInitiateAutoDiscovery": Destructiveness.DESTRUCTIVE,
    "#DellLCService.ReInitiateDHS": Destructiveness.DESTRUCTIVE,
    "#Volume.CheckConsistency": Destructiveness.DESTRUCTIVE,
    "#DellRaidService.CancelBackgroundInitialization": Destructiveness.DESTRUCTIVE,
    "#DellRaidService.CancelCheckConsistency": Destructiveness.DESTRUCTIVE,
    "#DellRaidService.CancelRebuildPhysicalDisk": Destructiveness.DESTRUCTIVE,
    "#DellLCService.ExposeiSMInstallerToHostOS": Destructiveness.DESTRUCTIVE,
    "#DellRaidService.ClearControllerPreservedCache": Destructiveness.IRREVERSIBLE,
    "#DellRaidService.ClearForeignConfig": Destructiveness.IRREVERSIBLE,
    "#DellRaidService.ChangePDState": Destructiveness.DESTRUCTIVE,
    "#DellRaidService.PrepareToRemove": Destructiveness.DESTRUCTIVE,
    "#DellRaidService.RebuildPhysicalDisk": Destructiveness.DESTRUCTIVE,
    "#CertificateService.ReplaceCertificate": Destructiveness.DESTRUCTIVE,
    "#SecureBoot.ResetKeys": Destructiveness.DESTRUCTIVE,
    "#SecureBootDatabase.ResetKeys": Destructiveness.DESTRUCTIVE,
    "#NvidiaDebugToken.InstallToken": Destructiveness.DESTRUCTIVE,
    "#UpdateService.SimpleUpdate": Destructiveness.DESTRUCTIVE,
    "#UpdateService.StartUpdate": Destructiveness.DESTRUCTIVE,
    "#SmcNodeManager.ClearAllPolicies": Destructiveness.DESTRUCTIVE,
    "#DellMetricService.ControlMetrics": Destructiveness.DESTRUCTIVE,
    "#DellMetricService.ExportThermalHistory": Destructiveness.DESTRUCTIVE,
    "#DelliDRACCardService.DisableiLKM": Destructiveness.DESTRUCTIVE,
    "#DelliDRACCardService.DisableSEKM": Destructiveness.DESTRUCTIVE,
    "#DelliDRACCardService.EnableiLKM": Destructiveness.DESTRUCTIVE,
    "#DelliDRACCardService.EnableSEKM": Destructiveness.DESTRUCTIVE,
    "#DelliDRACCardService.Rekey": Destructiveness.DESTRUCTIVE,
    "#DelliDRACCardService.iLKMToSEKMTransition": Destructiveness.DESTRUCTIVE,
    "#DelliDRACCardService.DeleteGroup": Destructiveness.DESTRUCTIVE,
    "#DelliDRACCardService.JoinGroup": Destructiveness.DESTRUCTIVE,
    "#DelliDRACCardService.RemoveSelf": Destructiveness.DESTRUCTIVE,

    # irreversible: data loss or one-way security change — needs the extra token
    "#Drive.SecureErase": Destructiveness.IRREVERSIBLE,
    "#DellRaidService.ImportForeignConfig": Destructiveness.IRREVERSIBLE,
    "#DellRaidService.UnLockSecureForeignConfig": Destructiveness.IRREVERSIBLE,
    "#Manager.ResetToDefaults": Destructiveness.IRREVERSIBLE,
    "#HpeServerChassis.FactoryResetMCTP": Destructiveness.IRREVERSIBLE,
    "#NvidiaRoTProtectedComponent.RevokeKeys": Destructiveness.IRREVERSIBLE,
    "#NvidiaRoTProtectedComponent.UpdateMinimumSecurityVersion": Destructiveness.IRREVERSIBLE,
    "#DellPersistentStorageService.DeletePartition": Destructiveness.IRREVERSIBLE,
    "#DellPersistentStorageService.FormatPartition": Destructiveness.IRREVERSIBLE,
    "#DellPersistentStorageService.InitializeMedia": Destructiveness.IRREVERSIBLE,
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
