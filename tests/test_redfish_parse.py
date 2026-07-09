"""Offline unit tests for RedfishManager.parse_json_respond_msg.

Regression for the swallowed-exception cleanup: a respond with no JSON body or
a non-object JSON body must degrade to an empty message list instead of raising
(previously the ``except ... as _: pass`` sites hid these cases silently; they
now log at debug and still return a usable object).

Author Mus spyroot@gmail.com
"""
from requests.models import Response

from redfish_ctl.redfish_manager import RedfishManager
from redfish_ctl.redfish_respond import RedfishRespondMessage
from tests.test_utils import create_json_resp


def _raw_response(body: bytes, status_code: int = 200) -> Response:
    resp = Response()
    resp._content = body
    resp.status_code = status_code
    resp._headers = {}
    resp.encoding = "utf-8"
    return resp


def test_parses_extended_info_message():
    """A well-formed redfish payload yields parsed extended messages."""
    data = {
        "@Message.ExtendedInfo": [
            {
                "Message": "The request completed successfully.",
                "MessageId": "Base.1.12.Success",
                "Severity": "OK",
                "Resolution": "None",
            }
        ]
    }
    resp = create_json_resp(data)
    parsed = RedfishManager.parse_json_respond_msg(resp)
    assert isinstance(parsed, RedfishRespondMessage)
    assert len(parsed.message_extended) == 1


def test_non_json_body_does_not_raise():
    """A non-JSON body returns an empty message list, not an exception."""
    resp = _raw_response(b"not json at all", status_code=400)
    parsed = RedfishManager.parse_json_respond_msg(resp)
    assert isinstance(parsed, RedfishRespondMessage)
    assert parsed.message_extended == []


def test_scalar_json_body_does_not_raise():
    """A scalar JSON body (TypeError on membership test) is handled gracefully."""
    resp = _raw_response(b"42", status_code=200)
    parsed = RedfishManager.parse_json_respond_msg(resp)
    assert isinstance(parsed, RedfishRespondMessage)
    assert parsed.message_extended == []


def test_json_without_extended_info_is_empty():
    """Valid JSON lacking ExtendedInfo yields an empty message list."""
    resp = create_json_resp({"SomeOtherKey": "value"})
    parsed = RedfishManager.parse_json_respond_msg(resp)
    assert parsed.message_extended == []
