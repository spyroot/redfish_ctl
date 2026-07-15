"""Redfish implementation based
https://www.dmtf.org/standards/REDFISH

A mapping redfish error python object.

Author Mus spyroot@gmail.com
"""
from typing import List, Optional

from .redfish_respond import RedfishRespondMessage


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

        :param message_id: Error or message.
        :param message: Human-readable error message that indicates the semantics associated with the error
        :param related: Substitution parameter values for the message.
        If the parameterized message defines a MessageId , the service shall include the MessageArgs in the response.
        :param message_args: Substitution parameter values for the message.
                            If the parameterized message defines a MessageId ,
                            the service shall include the MessageArgs in the response.
        :param message_severity: Severity of the error.
        :param severity: Severity of the error
        :param resolution: Recommended actions to take to resolve the error
        """
        if message_args is None:
            message_args = []

        self.message_id = message_id
        self.message = message
        self.related = related
        self.resolution = resolution
        # deprecated
        self.severity = severity
        self.message_severity = message_severity

        if message_args is None:
            self.message_args = []
        else:
            self.message_args = message_args

        self.message_count = 0


class RedfishErrorMessage(RedfishMessage):
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
        :param message_id: str: error msg id
        :param message:
        :param related:
        :param message_args:
        :param message_severity:
        :param severity:
        :param resolution:
        """
        super().__init__(
            message_id=message_id,
            message=message,
            related=related,
            message_args=message_args,
            message_severity=message_severity,
            severity=severity,
            resolution=resolution
        )


class RedfishError(RedfishRespondMessage):
    """
    Redfish error.  Please check for DSP0266. In high level it just more verbose
    chatty error respond.  How useful is that I'm not 100 sure :-)

    Note on top of describe properties, object store original http code server responded.
    So caller can make a decision what to do, also store _exception_msg in case
    JSON Decoder failed to decode error. ( this mainly if server responded with some dodgy
    replay)
    """

    def __init__(self,
                 http_status_code: int,
                 code: Optional[str] = "",
                 message: Optional[str] = "root",
                 message_extended: List[RedfishErrorMessage] = None,
                 exception_msg: Optional[str] = ""):
        """
        Redfish specs defines a verbose output. Motivation that is has more information
        about the error as possible.  It also defines multiply errors.
        :param http_status_code: HTTP status code the server returned.
        :param code: a string
        :param message: Displays a human-readable error message that corresponds
                        to the message in the message registry.
        :param message_extended: list of redfish that describe one or more error messages.
        :param exception_msg: raw error text for a JSON-decode failure; accepted for
                        signature parity with the base class but not forwarded to it here.
        """
        super().__init__(
            http_status_code=http_status_code,
            code=code, message=message,
            message_extended=message_extended,
        )
        super().__init__(http_status_code=http_status_code,
                         code=code, message=message,
                         message_extended=message_extended)

    @staticmethod
    def new_msg():
        """Return a fresh empty :class:`RedfishErrorMessage`.

        :return: a new, empty error-message object.
        """
        return RedfishErrorMessage()

    @property
    def message_extended(self) -> list[RedfishMessage]:
        """return a list of error message based on spec
        :return:  RedfishErrorMessage
        """
        return self._message_extended

    @staticmethod
    def _fmt_message(m) -> str:
        """Render one extended-info entry to a readable string, never raising.

        ``message_extended`` may hold either parsed ``RedfishMessage`` objects or
        the raw JSON dicts from the service, and a Redfish message legitimately
        carries only a ``MessageId`` + ``MessageArgs`` with no human-readable
        ``Message`` (e.g. iLO ``PropertyNotWritableOrUnknown``). Fall back to the
        id + args in that case so the error stays informative.

        :param m: an extended-info entry, either a ``RedfishMessage`` or a raw dict.
        :return: the rendered message string, or ``""`` when nothing is renderable.
        """
        try:
            if isinstance(m, dict):
                msg = m.get("Message")
                mid = m.get("MessageId", "") or ""
                args = m.get("MessageArgs") or []
            else:
                msg = getattr(m, "message", None)
                mid = getattr(m, "message_id", "") or ""
                args = getattr(m, "message_args", None) or []
            if msg:
                return str(msg)
            if mid:
                return f"{mid} {list(args)}" if args else str(mid)
            return ""
        except Exception:
            return ""

    def __str__(self) -> str:
        """Human-readable, never-raising rendering of the Redfish error.

        Combines the top-level message, each extended-info entry, and the HTTP
        status. Robust to ``message_extended`` being raw dicts or objects, and to
        a missing/None ``Message`` — the failure mode that previously made
        ``str(exception)`` raise and surface as ``<exception str() failed>``.

        :return: the combined human-readable error text, suffixed with the HTTP
            status when one is present.
        """
        parts = []
        top = getattr(self, "_message", None)
        if top and top != "root":
            parts.append(str(top))
        for m in (self._message_extended or []):
            frag = self._fmt_message(m)
            if frag:
                parts.append(frag)
        exc = getattr(self, "_exception_msg", "") or getattr(self, "exception_msg", "")
        if exc:
            parts.append(str(exc))
        body = "; ".join(dict.fromkeys(p for p in parts if p)) or "Redfish error"
        code = getattr(self, "_status_code", None)
        return f"{body} (HTTP {code})" if code else body

    def __repr__(self) -> str:
        """Return the same human-readable rendering as :meth:`__str__`.

        :return: the formatted error string.
        """
        return self.__str__()

    @message_extended.setter
    def message_extended(self, value):
        """Set the list of extended-info messages.

        :param value: the extended-info messages to store.
        """
        self._message_extended = value
