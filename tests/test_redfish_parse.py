"""Offline unit tests for RedfishManager.parse_json_respond_msg.

Regression for the swallowed-exception cleanup: a respond with no JSON body or
a non-object JSON body must degrade to an empty message list instead of raising
(previously the ``except ... as _: pass`` sites hid these cases silently; they
now log at debug and still return a usable object).

Author Mus spyroot@gmail.com
"""
from unittest.mock import Mock

import pytest
from requests.models import Response

from redfish_ctl.redfish_manager import RedfishManager
from redfish_ctl.redfish_respond import RedfishRespondMessage
from redfish_ctl.redfish_respond_error import RedfishError
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


def test_unexpected_exception_propagates():
    """An exception outside the handled set must reach the caller.

    Regression for the ``finally: return`` cleanup (PEP 765): the return in
    the ``finally`` block swallowed every in-flight exception, not just the
    handled ``JSONDecodeError``/``TypeError``, so genuine faults surfaced as
    an empty-but-valid respond object.
    """
    resp = Mock(spec=Response)
    resp.status_code = 200
    resp.json.side_effect = RuntimeError("unexpected parser fault")

    with pytest.raises(RuntimeError, match="unexpected parser fault"):
        RedfishManager.parse_json_respond_msg(resp)


def test_parse_error_without_error_envelope_returns_redfish_error():
    """Non-standard JSON error payloads still honor the RedfishError contract."""
    resp = create_json_resp({"message": "plain upstream failure"}, status_code=502)

    parsed = RedfishManager.parse_error(resp)

    assert isinstance(parsed, RedfishError)
    assert parsed.status_code == 502
    assert parsed.message == "plain upstream failure"


def test_parse_error_scalar_json_returns_redfish_error():
    """Scalar JSON bodies do not escape parse_error as TypeError."""
    resp = _raw_response(b'"maintenance mode"', status_code=503)

    parsed = RedfishManager.parse_error(resp)

    assert isinstance(parsed, RedfishError)
    assert parsed.status_code == 503
    assert parsed.message == "maintenance mode"


def test_parse_error_preserves_code_message_and_extended_info():
    """A standard Redfish error envelope keeps code, message, and ExtendedInfo."""
    resp = create_json_resp(
        {
            "error": {
                "code": "Base.1.12.PropertyValueNotInList",
                "message": "The requested value is not in the allowable list.",
                "@Message.ExtendedInfo": [
                    {
                        "MessageId": "Base.1.12.PropertyValueNotInList",
                        "MessageArgs": ["BootSourceOverrideTarget"],
                        "Severity": "Warning",
                        "Resolution": "Choose a value from AllowableValues.",
                    }
                ],
            }
        },
        status_code=400,
    )

    parsed = RedfishManager.parse_error(resp)

    assert isinstance(parsed, RedfishError)
    assert parsed.status_code == 400
    assert parsed.code == "Base.1.12.PropertyValueNotInList"
    assert parsed.message == "The requested value is not in the allowable list."
    assert parsed.message_extended == [
        {
            "MessageId": "Base.1.12.PropertyValueNotInList",
            "MessageArgs": ["BootSourceOverrideTarget"],
            "Severity": "Warning",
            "Resolution": "Choose a value from AllowableValues.",
        }
    ]
