"""Vendor split of the write path, interrogated on the correct class per vendor.

Dell iDRAC classifies writes with its ``_http_code_mapping``; a Lifecycle
Controller job id can arrive in the Location header or response body and
completion uses Dell's job model. Other vendors use the shared DMTF path, which
supports synchronous 2xx responses and the standard 202 + TaskService Location
form. The write methods are therefore a deliberate duplicate, one implementation
per semantic family, and MRO must never cross-wire them:

* a Dell instance reaching the base's job-less ``base_request_respond`` would
  silently stop extracting/polling job ids;
* a non-Dell instance reaching Dell's ``read_api_respond`` would ``AttributeError``
  on ``_http_code_mapping`` (built only in ``IDracManager.__init__``).

These tests pin resolution on the runtime command composition selected by the
CLI manager. The same DMTF ``MetricReports`` command must resolve through Dell's
transport when selected by ``IDracManager`` and through the neutral transport
when selected by ``SupermicroManager``. The concrete Supermicro exporter stays
on that vendor manager directly.

Author Mus spyroot@gmail.com
"""
import logging

import pytest

from redfish_ctl.cmd_exceptions import ResourceNotFound
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.redfish_shared import RedfishApiRespond
from redfish_ctl.supermico_manager import SupermicroManager
from redfish_ctl.telemetry.supermicro.cmd_exporter import Exporter
from redfish_ctl.telemetry.supermicro.cmd_metric_reports import MetricReports

DELL_METRIC_REPORTS = IDracManager._runtime_command_class(MetricReports)
SUPERMICRO_METRIC_REPORTS = SupermicroManager._runtime_command_class(
    MetricReports)

# The write methods that must be a per-family dup (same name, different owner).
WRITE_METHODS = [
    "base_request_respond", "base_post", "base_patch", "base_delete",
    "api_post_call", "api_patch_call", "api_delete_call",
    "default_post_success", "default_patch_success", "default_delete_success",
    "_redact_sensitive_payload",
]


def _provider(cls, name):
    """Return the name of the class in ``cls.__mro__`` that defines ``name``.

    This is exactly the lookup ``self.<name>`` performs at call time, so it
    proves which implementation a real instance of ``cls`` would run.

    :param cls: the command class to interrogate.
    :param name: the method name to resolve.
    :return: the owning class name, or None when no class in the MRO defines it.
    """
    for klass in cls.__mro__:
        if name in klass.__dict__:
            return klass.__name__
    return None


@pytest.mark.parametrize("name", WRITE_METHODS)
def test_dell_selected_dmtf_command_resolves_write_to_idrac_manager(name):
    """A Dell-selected DMTF command resolves writes to ``IDracManager``.

    If any resolved to ``RedfishManager`` the Dell write would lose its
    job-id/``fetch_task`` handling -- the cross-wire that breaks Dell.

    :param name: the write method under test.
    :return: None.
    """
    assert _provider(DELL_METRIC_REPORTS, name) == "IDracManager"


@pytest.mark.parametrize("command", [Exporter, SUPERMICRO_METRIC_REPORTS])
@pytest.mark.parametrize("name", WRITE_METHODS)
def test_non_dell_command_resolves_write_to_redfish_manager(command, name):
    """Supermicro and generic commands resolve every write method to the base.

    Both must inherit the generic DMTF write chain on ``RedfishManager``, not
    Dell's Lifecycle Controller job handling.

    :param command: the non-Dell command class under test.
    :param name: the write method under test.
    :return: None.
    """
    assert _provider(command, name) == "RedfishManager"


def test_read_api_respond_is_dell_only():
    """``read_api_respond`` needs Dell-only ``_http_code_mapping`` state and must
    not exist on a non-Dell command, or it would ``AttributeError`` there.

    :return: None.
    """
    assert _provider(DELL_METRIC_REPORTS, "read_api_respond") == "IDracManager"
    assert _provider(Exporter, "read_api_respond") is None
    assert _provider(SUPERMICRO_METRIC_REPORTS, "read_api_respond") is None


@pytest.mark.parametrize("name", ["job_id_from_respond", "job_id_from_response"])
def test_dell_body_job_id_parsers_are_dell_only(name):
    """Lifecycle Controller body-JID parsing must not leak into DMTF managers."""
    assert _provider(DELL_METRIC_REPORTS, name) == "IDracManager"
    assert _provider(Exporter, name) is None
    assert _provider(SUPERMICRO_METRIC_REPORTS, name) is None


class _FakeResp:
    """Minimal ``requests.Response`` stand-in for the write-path smoke."""

    def __init__(self, status_code, headers=None, body=None):
        """Record the fields the write path reads.

        :param status_code: the HTTP status code to report.
        :param headers: response headers (used for the Location task form).
        :param body: the JSON body returned by :meth:`json`.
        :return: None.
        """
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body or {}

    def json(self):
        """Return the canned JSON body.

        :return: the response body mapping.
        """
        return self._body


def _generic_instance():
    """Build a non-Dell command instance carrying only the attributes the write
    path touches, so no BMC or network is required.

    :return: an uninitialised Supermicro-selected DMTF command instance.
    """
    inst = object.__new__(SUPERMICRO_METRIC_REPORTS)
    inst.logger = logging.getLogger("test_write_path_vendor_split")
    inst.json_content_type = {"Content-Type": "application/json"}
    inst._default_method = "https://"
    inst._redfish_ip = "10.0.0.1"
    inst._port = 443
    return inst


def test_generic_synchronous_write_returns_success_no_task(monkeypatch):
    """A synchronous 204 write (Supermicro's shape) yields Success and no task id.

    :param monkeypatch: pytest fixture used to stub the HTTP transport.
    :return: None.
    """
    inst = _generic_instance()
    monkeypatch.setattr(inst, "api_post_call", lambda req, payload, hdr: _FakeResp(204))
    result, api_resp = inst.base_post("/redfish/v1/Foo", payload={"x": 1})
    assert api_resp is RedfishApiRespond.Success
    assert "task_id" not in (result.data or {})


def test_generic_dmtf_task_write_extracts_location_task_id(monkeypatch):
    """A DMTF 202 + Location write extracts the task id from the Location header.

    :param monkeypatch: pytest fixture used to stub the HTTP transport.
    :return: None.
    """
    inst = _generic_instance()
    monkeypatch.setattr(
        inst, "api_post_call",
        lambda req, payload, hdr: _FakeResp(
            202, headers={"Location": "/redfish/v1/TaskService/Tasks/7"}),
    )
    result, api_resp = inst.base_post("/redfish/v1/Foo", payload={})
    assert api_resp is RedfishApiRespond.AcceptedTaskGenerated
    assert result.data["task_id"] == "7"


def test_generic_write_error_surfaces_extended_info(monkeypatch):
    """An error status raises and carries ``@Message.ExtendedInfo`` via the shared
    ``parse_error`` -- the same operator-facing text as Dell.

    :param monkeypatch: pytest fixture used to stub the HTTP transport.
    :return: None.
    """
    inst = _generic_instance()
    body = {
        "error": {
            "code": "Base.1.0.GeneralError",
            "message": "top",
            "@Message.ExtendedInfo": [{"Message": "the detail the operator needs"}],
        }
    }
    monkeypatch.setattr(inst, "api_post_call", lambda req, payload, hdr: _FakeResp(404, body=body))
    with pytest.raises(ResourceNotFound) as exc:
        inst.base_post("/redfish/v1/Foo", payload={})
    assert "the detail the operator needs" in str(exc.value)


def test_generic_write_redacts_password_in_payload():
    """Password-like keys are masked before the payload is logged.

    :return: None.
    """
    inst = _generic_instance()
    redacted = inst._redact_sensitive_payload({"Password": "secret", "UserName": "admin"})
    assert redacted["Password"] == "***"
    assert redacted["UserName"] == "admin"
