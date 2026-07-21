"""Shared resource

This is shared Enum, Classes used by redfish_ctl.
Many classes mapped directly to JSON schem.

Author Mus spyroot@gmail.com

"""
import hashlib
import json
import threading
from enum import Enum, auto
from json import JSONEncoder
from typing import Optional

from .redfish_shared import RedfishApi, RedfishJson, RedfishJsonSpec


class ApiRequestType(Enum):
    """Each commands enum.
    """
    PrivilegeRegistry = auto()
    ComputeUpdate = auto()
    ComputeQuery = auto()
    ComputerSystemReset = auto()

    ConvertToRaid = auto()
    ConvertNoneRaid = auto()
    Drives = auto()
    DriveSecureErase = auto()
    VolumeInit = auto()
    VolumeQuery = auto()
    VolumeCreate = auto()
    VolumeDelete = auto()
    VolumeCheckConsistency = auto()
    DellRaidClearActions = auto()
    ImportOneTimeBoot = auto()

    # dell oem
    DellOemTask = auto()
    DellBiosDeviceRecovery = auto()
    DellLcQuery = auto()
    DellMetricActions = auto()
    DellLcExport = auto()
    DellLcIsmInstaller = auto()
    DellLcSupportAssistExport = auto()
    DellLcLogComment = auto()
    DellLcSupportAssistSchedule = auto()
    DellLcAutoDiscovery = auto()
    DellOemDisconnect = auto()
    DellCardSekmTest = auto()
    DellLcClearProvisioningServer = auto()

    RemoteServicesRssAPIStatus = auto()
    RemoteServicesAPIStatus = auto()
    DellLcNetworkShareTest = auto()
    DellLcSupportAssistStatus = auto()
    DellLcOsHealthUpdate = auto()
    TasksList = auto()

    ChangeBootOrder = auto()
    GetNetworkIsoAttachStatus = auto()
    OemAttach = auto()
    DellOemActions = auto()
    QueryIdrac = auto()
    RawGet = auto()

    BootOptions = auto()
    SystemConfigQuery = auto()
    IDracQuery = auto()

    # firmware
    FirmwareQuery = auto()
    FirmwareInventoryQuery = auto()
    UpdateServiceQuery = auto()
    UpdateStart = auto()
    PciDeviceQuery = auto()
    SystemQuery = auto()
    VirtualDiskQuery = auto()
    RaidServiceQuery = auto()
    DellRaidBlink = auto()
    DellRaidSpareActions = auto()
    DellRaidCancelActions = auto()
    DellRaidPatrolRead = auto()
    DellRaidConfigActions = auto()
    DellRaidRenameVD = auto()
    DellRaidPhysicalDiskActions = auto()
    DellRaidForeignConfigActions = auto()
    StorageQuery = auto()
    Tasks = auto()
    GetTask = auto()
    ImportSystem = auto()

    # virtual media
    VirtualMediaGet = auto()
    VirtualMediaInsert = auto()
    VirtualMediaEject = auto()
    SmcVirtualMediaMount = auto()
    CurrentBoot = auto()

    # storage
    StorageViewQuery = auto()
    StorageListQuery = auto()

    #
    BootOptionQuery = auto()
    BootOptionsClearPending = auto()
    BootOptionsPending = auto()

    QueryBootOption = auto()
    BootOneShot = auto()
    BootSettingsQuery = auto()
    EnableBootOptions = auto()
    Sensors = auto()
    ControlsQuery = auto()
    ControlResetDefaults = auto()
    Thermal = auto()
    Power = auto()
    PowerSmoothing = auto()
    PowerSmoothingAction = auto()
    WorkloadPower = auto()
    LeakDetectors = auto()
    EnvironmentMetrics = auto()
    ProcessorMetrics = auto()
    MemoryMetrics = auto()
    GpuMetrics = auto()
    MetricReports = auto()
    MetricReportDefinitions = auto()
    TelemetryClearReports = auto()
    TelemetryResetMetricDefinitions = auto()
    ComponentIntegrity = auto()
    SpdmMeasurements = auto()
    DellSystemLcdErrors = auto()
    NetworkAdapters = auto()
    NetworkAdapterReset = auto()
    DellNetworkAttributes = auto()
    NicFirmware = auto()
    NvLinkPorts = auto()
    Exporter = auto()
    ActionList = auto()
    EventSubmitTest = auto()
    HpeTestActions = auto()
    HpeKerberosKeytabImport = auto()
    DellCardTestActions = auto()
    NvidiaDebugToken = auto()
    HpeChassisActions = auto()
    DellCardCertExport = auto()
    DellCardHwProof = auto()
    DellCardKeyManagement = auto()
    DellPersistentInitializeMedia = auto()
    DellCardGroupActions = auto()
    EventServiceQuery = auto()
    SubscriptionCreate = auto()
    SubscriptionDelete = auto()
    SystemReset = auto()
    Logs = auto()
    LogClear = auto()
    LogCollectDiagnosticData = auto()
    LicenseInstall = auto()
    DellLicenseActions = auto()
    DellPersistentPartitionActions = auto()
    EthernetInterfaces = auto()
    SecureBoot = auto()
    CertificatesQuery = auto()
    CertificateGenerateCSR = auto()
    DellCardCsr = auto()
    FirmwareUpdate = auto()
    Triggers = auto()
    NetworkPorts = auto()
    DellSwitchPortRefresh = auto()
    OemInfo = auto()
    SmcNodeManagerClearPolicies = auto()
    DellVFlashStateChange = auto()
    ConsoleInfo = auto()
    SerialConsoleConfig = auto()
    BiosSnapshot = auto()
    BootState = auto()
    BmcScan = auto()
    ManagerTime = auto()
    WaitReady = auto()

    # boot sources
    BootSourcePending = auto()
    BootSourceUpdate = auto()
    BootSourceClear = auto()
    BootSourceRegistry = auto()
    BootQuery = auto()

    GetAttachStatus = auto()
    DellOemNetIsoBoot = auto()
    DellOemDetach = auto()
    DellSoftwareUpdateSchedule = auto()
    TaskGet = auto()

    # attribute
    AttributesQuery = auto()
    AttributesUpdate = auto()
    AttributeClearPending = auto()

    # manager
    ManagerQuery = auto()
    ManagerNetworkProtocol = auto()
    NtpSet = auto()
    IdentifyLed = auto()
    ManagerReset = auto()

    # bios related
    BiosRegistry = auto()
    BiosProfile = auto()
    BiosChangeSettings = auto()
    BiosResetDefault = auto()
    BiosClearPending = auto()
    BiosQueryPending = auto()
    BiosQuery = auto()

    # query account
    QueryAccount = auto()
    QueryAccounts = auto()
    QueryAccountService = auto()
    AccountCreate = auto()
    AccountUpdate = auto()
    AccountDelete = auto()
    AccountImportSSHKey = auto()

    ChassisQuery = auto()
    ChassisReset = auto()
    ChassisUpdate = auto()
    AssetTagSet = auto()

    JobGet = auto()
    JobDel = auto()
    Jobs = auto()
    JobApply = auto()
    JobWatch = auto()
    JobServices = auto()

    #  dell services
    JobRmDellServices = auto()
    JobDellServices = auto()
    DellJobQueueSetup = auto()

    Discovery = auto()
    FleetInventory = auto()
    CapabilityReport = auto()


class ScheduleJobType(Enum):
    """Each commands enum, based on redfish spec.
    """
    NoReboot = auto()
    AutoReboot = auto()
    OnReset = auto()
    Immediate = auto()


class RedfishActionEncoder(JSONEncoder):
    """JSON decoder used to serialize nested dicts.
    """

    def default(self, obj):
        """Serialize an object by returning its ``__dict__``.

        :param obj: the object being serialized.
        :return: the object's ``__dict__`` for JSON encoding.
        """
        return obj.__dict__


class RedfishAction:
    """Action discovery encapsulate each action to RedfishAction.
    """

    def __init__(self,
                 action_name: Optional[str] = "",
                 target: Optional[str] = "",
                 full_redfish_name: Optional[str] = ""):
        """Action discovered from json respond.

        :param action_name: short Redfish action name.
        :param target: the action target URI to invoke.
        :param full_redfish_name: fully qualified Redfish action name.
        """
        super().__init__()
        self.action_name = action_name
        self.full_redfish_name = full_redfish_name
        self.target = target
        self.args = None

    def __iter__(self):
        """Yield the action's (key, value) pairs for dict conversion."""
        yield from {
            "action_name": self.action_name,
            "full_redfish_name": self.full_redfish_name,
            "target": self.target,
            "args": self.args,
        }.items()

    def add_action_arg(self, arg_name, allowable_value):
        """Add action argument name and allowable values for
        arguments for each args.
        :param arg_name: redfish action argument name
        :param allowable_value: redfish action argument allowable values
        :return:
        """
        if self.args is None:
            self.args = {}
        self.args[arg_name] = allowable_value

    def toJSON(self):
        """Return the action serialized as a sorted, indented JSON string.

        :return: the action as a sorted, indented JSON string.
        """
        return json.dumps(self, default=lambda o: o.__dict__, sort_keys=True, indent=4)

    def __repr__(self):
        """Return the action's string representation.

        :return: the JSON string form of the action.
        """
        return self.__str__()

    def __str__(self):
        """Return the action as a compact JSON string.

        :return: the action encoded as a JSON string.
        """
        return json.dumps(dict(self), ensure_ascii=False)

    def to_json(self):
        """Return the action as a compact JSON string.

        :return: the action encoded as a JSON string.
        """
        return json.dumps(dict(self), ensure_ascii=False)


class Singleton(type):
    """One command instance per (class, BMC connection).

    Commands cache expensive per-BMC discovery (vendor, Redfish version,
    resource paths) on themselves, and a BMC round-trip can cost hundreds of
    milliseconds — so repeated invocations against the SAME BMC must reuse
    one instance. Keying by class alone made every later construction return
    the first BMC's instance with its credentials, transport, and cached
    state, so any multi-BMC path (fleet fan-out, proxy, a controller with
    several endpoints) silently read the first node. The key therefore
    fingerprints the connection; the password contributes only as a digest,
    never held as a plain dict key.
    """
    _instances = {}
    _lock = threading.Lock()

    @staticmethod
    def _connection_key(cls, args, kwargs):
        """Build the cache key fingerprinting a class and its BMC connection.

        :param cls: the command class being instantiated.
        :param args: positional constructor arguments.
        :param kwargs: keyword constructor arguments carrying the connection fields.
        :return: a tuple key; the password contributes only as a SHA-256 digest.
        """
        password = str(kwargs.get("idrac_password", "") or "")
        return (
            cls,
            args,
            kwargs.get("idrac_ip", ""),
            kwargs.get("idrac_username", ""),
            hashlib.sha256(password.encode()).hexdigest(),
            kwargs.get("idrac_port", 443),
            bool(kwargs.get("is_http", False)),
            bool(kwargs.get("insecure", True)),
            str(kwargs.get("x_auth", "") or ""),
        )

    def __call__(cls, *args, **kwargs):
        """Return the cached instance for this class+connection, building one once.

        :return: the singleton instance for the (class, connection) key.
        """
        key = Singleton._connection_key(cls, args, kwargs)
        inst = cls._instances.get(key)
        if inst is None:
            # Concurrent first-builds (fleet thread pool) must converge on
            # one instance.
            with Singleton._lock:
                inst = cls._instances.get(key)
                if inst is None:
                    inst = super(Singleton, cls).__call__(*args, **kwargs)
                    cls._instances[key] = inst
        return inst


class BootSource(Enum):
    """Boot sources."""
    Pxe = "Pxe"
    Floppy = "Floppy"
    CD = "CD"
    Usb = "Usb"
    Hdd = "Hdd"
    Utilities = "Utilities"
    UefiTarget = "UefiTarget"
    BiosSetup = "BiosSetup"


class BiosSetup(Enum):
    """Bios apply once etc"""
    Once = "Once"
    Continuous = "Continuous"
    Disabled = "Disabled"


class ResetType(Enum):
    """Reset types"""
    On = "On"
    ForceOff = "ForceOff"
    GracefulRestart = "GracefulRestart"
    PushPowerButton = "PushPowerButton"
    NMI = "NMI"
    # redfish
    ForceOn = "ForceOn"
    ForceRestart = "ForceRestart"
    PowerCycle = "PowerCycle"


class PowerState(Enum):
    """ Chassis power state
    """
    On = "On"
    Off = "Off"
    # this is not respected by the BMC in case of error
    Unknown = "Unknown"


class JobState(Enum):
    """IDRAC job states
    https://developer.dell.com/apis/2978/versions/4.xx/docs/101WhatsNew.md
    """
    New = "New"
    Scheduled = "Scheduled"
    Running = "Running"
    Completed = "Completed"
    CompletedWithErrors = "CompletedWithErrors"
    Downloaded = "Downloaded"
    Downloading = "Downloading"
    Scheduling = "Scheduling"
    Waiting = "Waiting"
    Failed = "Failed"
    RebootFailed = "RebootFailed"
    RebootCompleted = "RebootCompleted"
    RebootPending = "RebootPending"
    PendingActivation = "PendingActivation"
    Paused = "Paused"
    Unknown = "Unknown"


class CliJobTypes(Enum):
    """cli option for job types"""
    OsDeploy = "os"
    Bios_Config = "bios_config"
    FirmwareUpdate = "firmware_update"
    RebootNoForce = "reboot_no_force"


class IDRACJobType(Enum):
    """idrac job types
    """
    OSDeploy = "OSDeploy"
    Shutdown = "Shutdown"
    FirmwareUpdate = "FirmwareUpdate"
    RebootNoForce = "RebootNoForce"
    BIOSConfiguration = "BIOSConfiguration"
    FirmwareRollback = "FirmwareRollback"
    RepositoryUpdate = "RepositoryUpdate"
    RebootPowerCycle = "RebootPowerCycle"
    RAIDConfiguration = "RAIDConfiguration"
    NICConfiguration = "NICConfiguration"
    FCConfiguration = "FCConfiguration"
    iDRACConfiguration = "iDRACConfiguration"
    SystemInfoConfiguration = "SystemInfoConfiguration"
    InbandBIOSConfiguration = "InbandBIOSConfiguration"
    ExportConfiguration = "ExportConfiguration"
    ImportConfiguration = "ImportConfiguration"
    RemoteDiagnostics = "RemoteDiagnostics"
    LCLogExport = "LCLogExport"
    HardwareInventoryExport = "HardwareInventoryExport"
    FactoryConfigurationExport = "FactoryConfigurationExport"
    LicenseImport = "LicenseImport"
    LicenseExport = "LicenseExport"
    ThermalHistoryExport = "ThermalHistoryExport"
    LCConfig = "LCConfig",
    LCExport = "LCExport",
    SystemErase = "SystemErase"
    MessageRegistryExport = "MessageRegistryExport"
    UploadCustomDefaults = "UploadCustomDefaults"
    DPUConfig = "DPUConfig"
    ExportDeviceLog = "ExportDeviceLog"
    RealTimeNoRebootConfiguration = "RealTimeNoRebootConfiguration"
    Unknown = "Unknown"


class HTTPMethod(Enum):
    """Base HTTP methods."""
    GET = auto()
    POST = auto()
    PUSH = auto()
    PATCH = auto()
    DELETE = auto()


class RedfishChangePasswordReq:
    json = {
        "PasswordName": "Administrator | User",
        "OldPassword": "OldPasswordText",
        "NewPassword": "NewPasswordText"
    }


class Rest:
    pass


class RestMethodMapping:
    def __init__(self):
        """A generic api to map from a rest to supported HTTP method.
        """
        self._api_call = {}

    def add_api(self, a: Rest, method: HTTPMethod):
        """Map a REST resource to its supported HTTP method.

        :param a: the REST resource key.
        :param method: the HTTP method supported for the resource.
        """
        self._api_call[a] = method

    def method(self, a):
        """Return the HTTP method mapped to a REST resource.

        :param a: the REST resource key to look up.
        :return: the HTTP method registered for the resource.
        :raises KeyError: when the resource has no registered method.
        """
        return self._api_call[a]


# ChassisCollection.ChassisCollection


class SupportedScheduledJobs(Enum):
    actions = {
        "ComputerSystem.Reset": ""
                                "Chassis.Reset"
    }


class RedfishSupermicro:
    """Mapping redfish rest to supermicro
    """
    Sessions = f"{RedfishApi.Version}/SessionService/Sessions"
    BiosAttributeRegistry = f"{RedfishApi.Version}/Registries/BiosAttributeRegistry.v1_0_0"
    FirmwareInventoryBackup = f"{RedfishApi.Version}/UpdateService/FirmwareInventory/Backup_BIOS"
    BMC_Backup = f"{RedfishApi.Version}/UpdateService/FirmwareInventory/Backup_BMC"


class IdracJobSvcActions(Enum):
    """Dell IDRAC job services actions."""

    # The CreateRebootJob action is used for creating a reboot job.
    CreateRebootJob = "CreateRebootJob"
    # method is used for deleting jobs from the JobQueue or the job store
    DeleteJobQueue = "DeleteJobQueue"

    SetupJobQueue = "SetupJobQueue"
    SetDeleteOnCompletionTimeout = "SetDeleteOnCompletionTimeout"


class IdracResetActions(Enum):
    """Reset actions."""
    ComputerSystemReset = "ComputerSystem.Reset"
    ChassisReset = "Chassis.Reset"
    ManagerReset = "Manager.Reset"


class REDFISH_API:
    """
    Supported API actions
    """
    IDRAC_MANAGER = RedfishApi.Managers
    IDRAC_DELL_MANAGERS = f"{RedfishApi.Version}/Dell/Managers"
    Tasks = f"{RedfishApi.Version}/TaskService/Tasks/"

    IDRAC_LLC = "/iDRAC.Embedded.1/DellLCService"
    BiosRegistry = "/Bios/BiosRegistry"

    Chassis = f"{RedfishApi.Version}/Chassis"

    Jobs = "/Jobs"
    JobService = "JobService"
    TaskService = "TaskService"
    EventService = "EventService"
    UpdateService = "UpdateService"
    TelemetryService = "TelemetryService"
    DellJobService = "DellJobService"
    AccountService = "AccountService"
    DellLCService = "DellLCService"

    JobServiceQuery = f"{RedfishApi.Version}/{JobService}"
    TaskServiceQuery = f"{RedfishApi.Version}/{TaskService}"
    EventServiceQuery = f"{RedfishApi.Version}/{EventService}"
    UpdateServiceQuery = f"{RedfishApi.Version}/{UpdateService}"
    AccountServiceQuery = f"{RedfishApi.Version}/{AccountService}"
    TelemetryServiceQuery = f"{RedfishApi.Version}/{TelemetryService}"

    Accounts = f"{RedfishApi.Version}/{AccountService}/Accounts"
    Account = f"{RedfishApi.Version}/{AccountService}/Accounts/"

    DellOemJobService = f"/Oem/Dell/{DellJobService}"
    DellOemJobServiceAction = f"/Oem/Dell/{DellJobService}/Actions/DellJobService."
    # "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellAttributes/System.Embedded.1"
    # "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellAttributes/System.Embedded.1"

    BootSourcesRegistryQuery = f"/{RedfishApi.BootSources}/{RedfishApi.BootSourcesRegistry}"

    # /redfish/v1/Registries/ManagerAttributeRegistry/ManagerAttributeRegistry.v1_0_0.json
    # /redfish/v1/AccountService/Roles/{RoleId}
    # The value of the Id property of the Role resource
    BiosSettings = RedfishApi.BiosSettings
    # Base BIOS resource, relative to idrac_manage_servers
    # (e.g. /redfish/v1/Systems/System.Embedded.1) -> .../Bios. This matches the
    # BiosRegistry/BiosSettings members, which hang off the same /Bios base.
    BIOS = RedfishApi.Bios
    # COMPUTE_RESET = RedfishApi.ComputeReset
    BootOptions = "BootOptions"


# $select=SecurityCertificate.*

class REDFISH_JSON:
    """All keys we expect the BMC uses based on the specification.
    """
    Id = "Id"
    # Describes the source of the payload.
    Data_id = "@odata.id"
    # odata type
    Data_type = "@odata.type"
    # Displays the total number of Members in the Resource Collection
    Data_count = "@odata.count"
    # Describes the source of the payload.
    Data_content = "@odata.context"
    # Indicates the "nextLink" when the payload contains partial results
    Data_next = "@odata.nextLink"

    Actions = "Actions"
    Members = "Members"
    Datatime = "DateTime"
    Location = "Location"
    IDracFirmwareVersion = "FirmwareVersion"
    Links = RedfishJsonSpec.Links
    Attributes = RedfishJson.Attributes
    RegistryEntries = "RegistryEntries"

    #
    DateTimeLocalOffset = "DateTimeLocalOffset"
    FirmwareVersion = "FirmwareVersion"
    ManagerServers = "ManagerForServers"
    ManageChassis = "ManagerForChassis"
    LastResetTime = "LastResetTime"
    TimezoneName = "TimeZoneName"
    UUID = "UUID"

    # Job states
    JobState = "JobState"
    TaskState = "TaskState"
    TaskStatus = "TaskStatus"
    PercentComplete = "PercentComplete"

    ApplyTime = "ApplyTime"
    RedfishSettingsApplyTime = "@Redfish.SettingsApplyTime"
    MaintenanceWindowStartTime = "MaintenanceWindowStartTime"
    MaintenanceWindowDuration = "MaintenanceWindowDurationInSeconds"

    # Accounts
    AccountId = "Id"
    Username = "UserName"
    AccountEnabled = "Enabled"
    AccountTypes = "AccountTypes"
    AccountTypesOem = "OEMAccountTypes"
    PasswordExpiration = "PasswordExpiration"
    PasswordChangeRequired = "PasswordChangeRequired"
    AccountRole = "Role"
    AccountRoleId = "RoleId"

    # Chassis
    Reset = "Reset"
    ResetType = "ResetType"

    # chassis schema
    PowerState = "PowerState"


class JobApplyTypes:
    """Job apply types"""
    InMaintenance = "InMaintenanceWindowOnReset"
    AtMaintenance = "AtMaintenanceWindowStart"
    OnReset = "OnReset"
    Immediate = "Immediate"


class RedfishApiRespond(Enum):
    """We need report to a client either redfish created task and accepted
    or ok and success.  Note that some API has mismatch between
    200/204  hence it better differentiate each case
    """
    Ok = auto()
    Error = auto()
    Created = auto()
    Success = auto()
    AcceptedTaskGenerated = auto()


class ApiRespondString:
    """We need report to a client either redfish created task and accepted
    or ok and success.  Note that some API has mismatch between
    200/204  hence it better differentiate each case
    """
    Ok = "ok"
    Error = "error"
    Created = "created"
    Success = "success"
    AcceptedTaskGenerated = "accepted"


class BootSourceOverrideEnabled(Enum):
    """Boot source override modes.
    """
    Disabled = "Disabled"
    Continuous = "Continuous"
    Once = "Once"


class BootSourceOverrideMode(Enum):
    """Boot source
    """
    UEFI = "UEFI"
    Legacy = "Legacy"


class MediaTypes(Enum):
    """Virtual media types.
    """
    CD = "CD"
    DVD = "DVD"
    USBStick = "USBStick"


class DellBootSource:
    def __init__(self, device_id, name, enabled: Optional[bool] = True, index: Optional[int] = 1):
        """
        "Enabled": true,
        "Id": "BIOS.Setup.1-1#BootSeq#NIC.Slot.8-1#4f5d8523dbaffba918182fe3adb15032",
        "Index": 2,
        "Name": "NIC.Slot.8-1"

        :param device_id:
        :param name:
        :param index:
        :param enabled:
        """
        self._index = index
        self._name = name
        self._id = device_id
        self._enabled = enabled

    @property
    def Enabled(self) -> bool:
        """if the boot device is Enabled
        :return:
        """
        return self._enabled

    @property
    def Id(self) -> str:
        """The unique identifier of the boot device.

        :return: the boot device id.
        """
        return self._id

    @property
    def Index(self) -> int:
        """The index number of the boot device in the  order list
        :return:
        """
        return self._index

    @property
    def Name(self) -> str:
        """The fully qualified device descriptor (FQDD) of the boot device
        :return:
        """
        return self._name


class IdracRequestHeaders:
    http_x_auth_token = "X-AUTH-TOKEN"
    xsrf_token = "XSRF-TOKEN"


class IdracRespondHeaders:
    http_allow = "Allow"
    http_www_authentication = "WWW-Authenticate"
    http_www_authentication_realm = "Basic realm=\"RedfishService\""


class IdracRebootJobTypes(Enum):
    """IdracRebootJobTypes is reboot job types for CreateRebootJobReq
    """
    GracefulRebootWithForcedShutdown = "GracefulRebootWithForcedShutdown"
    GracefulRebootWithoutForcedShutdown = "GracefulRebootWithoutForcedShutdown"
    PowerCycle = "PowerCycle"


class CreateRebootJobReq:
    def __init__(self, reboot_job_type: IdracRebootJobTypes, target: str, title: str):
        """The CreateRebootJob action is used for creating a reboot job.
        :param reboot_job_type: IdracRebootJobTypes:  a reboot job type IdracRebootJobTypes
        :param target: Link to invoke action
        :param title: name
        """
        self.CreateRebootJob = {
            "RebootJobType": reboot_job_type.value,
            "target": target,
            "title": title
        }


class TestNetworkShareReq:
    def __init__(self,
                 host="downloads.dell.com",
                 share_type="HTTPS",
                 proxy_support="Off",
                 ignore_cert_warning="On"):
        """
        This a default test network share request type for DellLCService.TestNetworkShare.

        :param host:
        :param share_type:
        :param proxy_support:
        :param ignore_cert_warning:
        """
        self.network_share_req = {
            "IPAddress": host,
            "ShareType": share_type,
            "ProxySupport": proxy_support,
            "IgnoreCertWarning": ignore_cert_warning
        }
        self._success = 200
        self._method = HTTPMethod.POST

    @property
    def success(self):
        """The HTTP status code treated as success for this request.

        :return: the success status code (200).
        """
        return self._success

    @property
    def method(self):
        """The HTTP method used for this request.

        :return: the HTTP method (POST).
        """
        return self._method
