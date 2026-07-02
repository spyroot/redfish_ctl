"""Regression tests: a RedfishError must stringify without raising.

A Redfish error legitimately carries only MessageId + MessageArgs with no
human-readable Message (e.g. iLO ``PropertyNotWritableOrUnknown``), and
``message_extended`` may hold the raw JSON dicts from the service. The old
``__repr__`` did ``"\\n".join([m.message for m in ...])`` which raised
(dict has no ``.message``; ``None`` breaks ``join``) and surfaced as the useless
``<exception str() failed>``, hiding the real BMC error message.
"""
from idrac_ctl.redfish_respond_error import RedfishError


def test_str_with_messageid_only_dict():
    """An extended-info dict with only MessageId+MessageArgs renders the id + args."""
    e = RedfishError(400)
    e.message_extended = [{"MessageId": "iLO.2.19.PropertyNotWritableOrUnknown",
                           "MessageArgs": ["SSHKeys"]}]
    s = str(e)
    assert "PropertyNotWritableOrUnknown" in s
    assert "SSHKeys" in s
    assert "400" in s


def test_str_prefers_human_message():
    """A human-readable Message is used verbatim when present."""
    e = RedfishError(404)
    e.message_extended = [{"Message": "The resource was not found.",
                           "MessageId": "Base.1.0.ResourceMissing"}]
    assert "The resource was not found." in str(e)


def test_str_never_raises_on_garbage():
    """Odd/None/empty entries must not make str() raise."""
    e = RedfishError(500)
    e.message_extended = [None, {}, {"MessageArgs": ["x"]}, "weird"]
    s = str(e)  # must not raise
    assert isinstance(s, str) and "500" in s


def test_exception_carrying_redfish_error_is_readable():
    """A RedfishException built with a RedfishError renders the real message.

    This is the actual bug: previously str(exception) was '<exception str() failed>'.
    """
    from idrac_ctl.redfish_exceptions import RedfishException
    e = RedfishError(400)
    e.message_extended = [{"MessageId": "iLO.2.19.PropertyNotWritableOrUnknown",
                           "MessageArgs": ["SSHKeys"]}]
    assert "PropertyNotWritableOrUnknown" in str(RedfishException(e))
