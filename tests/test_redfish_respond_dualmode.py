"""Dual-mode regression tests for Redfish response parsing helpers."""

from redfish_ctl.redfish_respond import RedfishRespondMessage


def test_odata_count_map_uses_property_prefixed_count_keys():
    """List properties map only to their own <property>@odata.count key."""
    payload = {
        "Members": [{"@odata.id": "/redfish/v1/Systems/System.Embedded.1"}],
        "Members@odata.count": 1,
        "Messages": [{"MessageId": "Base.1.0.Success"}],
        "Messages@odata.count": 1,
        "Links": [],
        "Name": "System collection",
    }

    assert RedfishRespondMessage.redfish_odata_count_map(payload) == {
        "Members": "Members@odata.count",
        "Messages": "Messages@odata.count",
    }


def test_message_extended_accepts_message_id_without_message():
    """ExtendedInfo entries with only MessageId are parsed without KeyError."""
    response = RedfishRespondMessage(400)

    response.message_extended = [
        {
            "MessageId": "Base.1.0.GeneralError",
            "Severity": "Warning",
        }
    ]

    assert len(response.message_extended) == 1
    message = response.message_extended[0]
    assert message.message_id == "Base.1.0.GeneralError"
    assert message.message == ""
    assert message.severity == "Warning"


def test_message_extended_accepts_message_without_message_id():
    """ExtendedInfo entries with only Message still preserve the text."""
    response = RedfishRespondMessage(400)

    response.message_extended = [
        {
            "Message": "The request completed successfully.",
            "MessageArgs": "System.Embedded.1",
        }
    ]

    assert len(response.message_extended) == 1
    message = response.message_extended[0]
    assert message.message_id == ""
    assert message.message == "The request completed successfully."
    assert message.message_args == ["System.Embedded.1"]
