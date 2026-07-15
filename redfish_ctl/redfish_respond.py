"""Redfish implementation based
https://www.dmtf.org/standards/REDFISH

A mapping redfish error python object.

Author Mus spyroot@gmail.com
"""
from typing import Optional, List
from .redfish_shared import RedfishJsonMessage as jsonMessage


class RedfishMessage:
    """Generic redfish error"""

    def __init__(self,
                 message_id: Optional[str] = "",
                 message: Optional[str] = "",
                 related: Optional[List[str]] = None,
                 message_args: Optional[List[str]] = None,
                 message_severity: Optional[str] = "",
                 severity: Optional[str] = "",
                 resolution: Optional[str] = ""
                 ):
        """
        Each instance of a message object shall contain at least a MessageId ,
        together with any applicable MessageArgs , or a Message property that defines
        the complete human-readable error message.

        :param message_id:  message id
        :param message: Human-readable error message that indicates the semantics associated with the error
        :param related: Substitution parameter values for the message.
        If the parameterized message defines a MessageId , the service shall include the MessageArgs in the response.
        :param message_args: Substitution parameter values for the message.
                            If the parameterized message defines a MessageId ,
                            the service shall include the MessageArgs in the response.
        :param message_severity: Severity of the error.
        :param severity: Severity of the error
        :param resolution: recommended actions to take to resolve the error
        """
        if message_args is None:
            message_args = []

        if related is None:
            related = []

        self.message = message
        self.related = related

        self.message_id = message_id
        self.resolution = resolution

        # deprecated
        self.severity = severity
        self.message_severity = message_severity

        if message_args is None:
            self.message_args = []
        else:
            self.message_args = message_args

        self.message_count = 0
        self.related_count = 0
        self.message_args_count = 0


class RedfishRespondMessage:
    def __init__(self,
                 http_status_code: int,
                 code: Optional[str] = "",
                 message: Optional[str] = "root",
                 message_extended: List[RedfishMessage] = None,
                 exception_msg: Optional[str] = ""):
        """
        Redfish specs defines a verbose output.  THis generic respond msg.

        :param http_status_code: HTTP status code the server returned.
        :param code: a string
        :param message: Displays a human-readable error message that corresponds
                        to the message in the message registry.
        :param message_extended: list of redfish that describe one or more error messages.
        :param exception_msg: raw error text kept when JSON decoding of the error failed.
        """
        self._code = code
        self._message = message

        if message_extended is None:
            self._message_extended = []
        else:
            self._message_extended = message_extended

        self._status_code = http_status_code
        self._exception_msg = exception_msg

    @property
    def code(self) -> str:
        """code from error message from redfish error

        :return: the error code string.
        """
        return self._code

    @property
    def message(self) -> str:
        """Message from redfish error.
        :return:
        """
        return self._message

    @property
    def status_code(self) -> int:
        """http status code that issue error.
        :return:
        """
        return self._status_code

    @property
    def message_extended(self) -> list[RedfishMessage]:
        """return a list it encapsulates Message.ExtendedInfo based
        on redfish spec
        :return:  RedfishErrorMessage
        """
        return self._message_extended

    @message.setter
    def message(self, value: str) -> None:
        """Set the top-level human-readable message.

        :param value: the message text.
        """
        self._message = value

    @staticmethod
    def new_msg():
        """Return a fresh empty :class:`RedfishMessage`.

        :return: a new, empty message object.
        """
        return RedfishMessage()

    @staticmethod
    def is_redfish_msg(obj):
        """
        :param obj:
        :return:
        """
        return obj is not None and isinstance(obj, dict) \
            and jsonMessage.MessageExtendedInfo in obj

    @staticmethod
    def redfish_odata_count_map(json_dict: dict) -> dict[str]:
        """Return a dict that store for each list property it
           respected  @odata.counts property
        :param json_dict:
        :return:
        """
        odata_counts_keys = {}
        for k in json_dict.keys():
            # for each key that value is list we expect data count
            if isinstance(json_dict[k], list):
                odata_count = f"{k}@odata.count"
                if odata_count in json_dict:
                    odata_counts_keys[k] = odata_count

        return odata_counts_keys

    @message_extended.setter
    def message_extended(self, value) -> None:
        """a value must a list.
        :param value:
        :return: None
        """

        json_list = value
        if value is None:
            json_list = []

        if isinstance(value, dict) and \
                jsonMessage.MessageExtendedInfo in value:
            json_list = value[jsonMessage.MessageExtendedInfo]

        if not isinstance(json_list, list):
            return

        for v in json_list:
            if not isinstance(v, dict):
                continue

            # data_count_map = self.redfish_odata_count_map(v)
            # msg.add_property(data_count_map)
            msg = RedfishMessage()
            if jsonMessage.MessageId in v:
                msg.message_id = v[jsonMessage.MessageId]
            if jsonMessage.Message in v:
                msg.message = v[jsonMessage.Message]

            if jsonMessage.Message.lower() in v:
                msg.message = v[jsonMessage.Message.lower()]

            # list of args
            if jsonMessage.MessageArgs in v:
                if isinstance(v[jsonMessage.MessageArgs], list):
                    msg.message_args = v[jsonMessage.MessageArgs]
                else:
                    msg.message_args.append(v[jsonMessage.MessageArgs])

            if jsonMessage.MessageSeverity in v:
                msg.message_severity = v[jsonMessage.MessageSeverity]
            if jsonMessage.Severity in v:
                msg.severity = v[jsonMessage.Severity]
            if jsonMessage.Severity.lower() in v:
                msg.severity = v[jsonMessage.Severity.lower()]
            if jsonMessage.Resolution in v:
                msg.resolution = v[jsonMessage.Resolution]
            if jsonMessage.Resolution.lower() in v:
                msg.resolution = v[jsonMessage.Resolution.lower()]

            if jsonMessage.MessageArgsCount in v:
                msg.message_count = int(v[jsonMessage.MessageArgsCount])

            if jsonMessage.RelatedPropertiesCount in v:
                msg.related_count = int(v[jsonMessage.RelatedPropertiesCount])

            self._message_extended.append(msg)

    def __repr__(self) -> str:
        """Render the extended-info messages as newline-joined text.

        :return: each extended message on its own line, terminated by a newline.
        """
        msgs = [m.message for m in self._message_extended]
        return "\n".join(msgs) + "\n"
