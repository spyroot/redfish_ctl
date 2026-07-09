"""Offline unit tests for the Redfish response/error model classes.

Author Mus spyroot@gmail.com
"""
from redfish_ctl.redfish_respond import RedfishMessage, RedfishRespondMessage
from redfish_ctl.redfish_respond_error import RedfishError, RedfishErrorMessage

EXTENDED = {
    "@Message.ExtendedInfo": [
        {
            "MessageId": "Base.1.12.Success",
            "Message": "The request completed successfully.",
            "Severity": "OK",
            "Resolution": "None",
        }
    ]
}


def test_respond_message_basic_properties():
    """Status code, code, and message are exposed; extended defaults to []."""
    msg = RedfishRespondMessage(200, code="Base.1", message="ok")
    assert msg.status_code == 200
    assert msg.code == "Base.1"
    assert msg.message == "ok"
    assert msg.message_extended == []


def test_message_setter():
    """The message property is settable."""
    msg = RedfishRespondMessage(200)
    msg.message = "changed"
    assert msg.message == "changed"


def test_message_extended_setter_parses_extended_info():
    """Assigning an ExtendedInfo dict parses it into RedfishMessage objects."""
    msg = RedfishRespondMessage(200)
    msg.message_extended = EXTENDED
    parsed = msg.message_extended
    assert len(parsed) == 1
    assert parsed[0].message_id == "Base.1.12.Success"
    assert parsed[0].message == "The request completed successfully."
    assert parsed[0].severity == "OK"
    assert parsed[0].resolution == "None"


def test_message_extended_setter_none_is_empty():
    """Assigning None yields an empty extended list, not an error."""
    msg = RedfishRespondMessage(200)
    msg.message_extended = None
    assert msg.message_extended == []


def test_is_redfish_msg():
    """is_redfish_msg detects the ExtendedInfo envelope shape."""
    assert RedfishRespondMessage.is_redfish_msg(EXTENDED) is True
    assert RedfishRespondMessage.is_redfish_msg({"a": 1}) is False
    assert RedfishRespondMessage.is_redfish_msg(None) is False


def test_new_msg_factory():
    """new_msg returns a blank RedfishMessage."""
    assert isinstance(RedfishRespondMessage.new_msg(), RedfishMessage)


def test_redfish_error_repr_and_extended():
    """RedfishError exposes its extended messages and renders them in repr."""
    err = RedfishError(
        400,
        message_extended=[RedfishErrorMessage(message="disk is busy")],
    )
    assert err.status_code == 400
    assert "disk is busy" in repr(err)
    assert err.message_extended[0].message == "disk is busy"


def test_redfish_error_new_msg():
    """RedfishError.new_msg returns a blank RedfishErrorMessage."""
    assert isinstance(RedfishError.new_msg(), RedfishErrorMessage)


def test_redfish_message_defaults():
    """RedfishMessage defaults message_args to an empty list."""
    m = RedfishMessage()
    assert m.message_args == []
    assert m.message_count == 0
