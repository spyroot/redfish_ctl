from enum import auto, Enum


class RedfishActions(Enum):
    """Redfish actions.
    """
    BiosReset = "Bios.ResetBios"
    SimpleUpdate = "SimpleUpdate"
    ManagerReset = "#Manager.Reset"
    ComputerSystemReset = "ComputerSystem.Reset"


class RedfishApiRespond(Enum):
    """This base redfish api error.
    IDRAC overwrite so in case of different semantics
    we don't have special cases.
    """
    Ok = auto()
    Error = auto()
    Success = auto()
    AcceptedTaskGenerated = auto()


class RedfishJsonSpec:
    """ Redfish json scheme
    """
    Links = "Links"
    Location = "Location"
    WwwAuthentication = "WWW-Authenticate"


class RedfishJsonMessage:
    """Redfish respond json scheme
    either success or error
    """
    Message = "Message"
    MessageId = "MessageId"
    MessageArgs = "MessageArgs"
    MessageSeverity = "MessageSeverity"
    MessageExtendedInfo = "@Message.ExtendedInfo"
    Severity = "Severity"
    Resolution = "Resolution"

    MessageArgsCount = "MessageArgs@odata.count"
    RelatedPropertiesCount = "RelatedProperties@odata.count"


class RedfishJson:
    # json list of resource links
    Links = "Links"
    # json list of member of particular API
    Members = "Members"
    # json data time object
    Datatime = "DateTime"
    # json location
    Location = "Location"
    # json actions list
    Actions = "Actions"
    # json attribute list
    Attributes = "Attributes"
    # api entries for registry
    RegistryEntries = "RegistryEntries"

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
    #
    MembersCount = "Members@odata.count"
    # This property is an array of references to the systems that this manager has control over.
    ManagerServers = "ManagerForServers"
    # This property is an array of references to the chassis that this manager has control over.
    ManagerForChassis = "ManagerForChassis"
    # Manager.Reset
    # redfish response message.
    MessageExtendedInfo = "@Message.ExtendedInfo"


class RedfishApi:
    """
    """
    Actions = "Actions"
    Settings = "Settings"
    Managers = "Managers"
    Systems = "Systems"
    Chassis = "Chassis"
    AccountService = "AccountService"

    BootSources = "BootSources"
    BootSourcesRegistry = "BootSourcesRegistry"

    Version = "/redfish/v1"
    Managers = f"{Version}/{Managers}"
    Systems = f"{Version}/{Systems}"
    Chassis = f"{Version}/{Chassis}"

    Bios = "/Bios"
    UpdateService = f"{Version}/UpdateService"
    UpdateServiceAction = f"{UpdateService}/{Actions}/{RedfishActions.SimpleUpdate.value}"

    BiosSettings = f"{Bios}/{Settings}"
    BiosReset = f"{Bios}/{Settings}/{Actions}/{RedfishActions.BiosReset.value}"
    ManagerAccount = f"{Version}/{AccountService}"
    CHASSIS = "/Chassis"
