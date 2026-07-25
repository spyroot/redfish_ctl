"""Vendor split of the write path, interrogated on the correct class per vendor.

Dell iDRAC builds every write on the job/task system: status is classified with
the Dell ``_http_code_mapping``, a job id comes back in the Location header OR the
response body, and completion is polled with ``fetch_task`` over the Dell job
model. Supermicro/HPE have no job system at all -- a write returns 200/204
synchronously with no task. So the write methods are a deliberate DUP, one
implementation per family, and MRO must resolve each family to its own copy and
never cross-wire:

* a Dell instance reaching the base's job-less ``base_request_respond`` would
  silently stop extracting/polling job ids;
* a non-Dell instance reaching Dell's ``read_api_respond`` would ``AttributeError``
  on ``_http_code_mapping`` (built only in ``IDracManager.__init__``).

These tests pin the resolution on the *actual* command classes -- a Dell command
(``SystemQuery`` -> ``IDracManager``), a Supermicro command (``Exporter`` ->
``SupermicroManager``) and a generic DMTF command (``MetricReports`` ->
``RedfishManager``) -- and smoke the generic synchronous write path.

Author Mus spyroot@gmail.com
"""
import logging

import pytest

from redfish_ctl.cmd_exceptions import ResourceNotFound
from redfish_ctl.redfish_shared import RedfishApiRespond
from redfish_ctl.system.cmd_system import SystemQuery                          # Dell / IDracManager
from redfish_ctl.telemetry.supermicro.cmd_exporter import Exporter            # Supermicro / SupermicroManager
from redfish_ctl.telemetry.supermicro.cmd_metric_reports import MetricReports  # generic / RedfishManager

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
def test_dell_command_resolves_write_to_idrac_manager(name):
    """A Dell command resolves every write method to ``IDracManager``.

    If any resolved to ``RedfishManager`` the Dell write would lose its
    job-id/``fetch_task`` handling -- the cross-wire that breaks Dell.

    :param name: the write method under test.
    :return: None.
    """
    assert _provider(SystemQuery, name) == "IDracManager"


@pytest.mark.parametrize("command", [Exporter, MetricReports])
@pytest.mark.parametrize("name", WRITE_METHODS)
def test_non_dell_command_resolves_write_to_redfish_manager(command, name):
    """Supermicro and generic commands resolve every write method to the base.

    Neither vendor has a job system, so both must inherit the generic
    synchronous write chain on ``RedfishManager``, not Dell's.

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
    assert _provider(SystemQuery, "read_api_respond") == "IDracManager"
    assert _provider(Exporter, "read_api_respond") is None
    assert _provider(MetricReports, "read_api_respond") is None


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

    :return: an uninitialised :class:`MetricReports` with the write-path attrs set.
    """
    inst = object.__new__(MetricReports)
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
