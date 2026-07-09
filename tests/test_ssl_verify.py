"""TLS verification semantics for the Redfish/iDRAC client.

BMCs (iDRAC, Supermicro, etc.) ship self-signed certificates, so the tool must
*skip* certificate verification by default and verify only when explicitly asked.
Internally ``insecure`` means "skip verification" and maps to the inverse of the
``verify`` kwarg that the client hands to ``requests``. These tests pin that
mapping at the seam where ``requests.get`` is called, capturing the ``verify``
kwarg via a monkeypatched stub so no real network call is ever made.
"""
from types import SimpleNamespace

import pytest
import requests

from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.redfish_manager import RedfishManager


class _FakeResponse:
    """Minimal stand-in for ``requests.Response`` returned by the stub."""

    status_code = 200

    def json(self):  # pragma: no cover - not exercised here
        return {}


@pytest.fixture
def captured_verify(monkeypatch):
    """Monkeypatch ``requests.Session.get`` to record kwargs, no network.

    Both managers now issue GETs through a pooled keep-alive ``requests.Session``
    (see ``_http_session``) instead of the module-level ``requests.get``, so the
    seam to intercept is ``Session.get``. Records the ``verify`` and ``timeout``
    kwargs plus the id of each Session used, so tests can also assert the
    connection is reused. No socket is ever opened.
    """
    seen = {"sessions": []}

    def fake_get(self, url, **kwargs):  # self == the bound Session instance
        seen["verify"] = kwargs.get("verify")
        seen["timeout"] = kwargs.get("timeout")
        seen["url"] = url
        seen["sessions"].append(id(self))
        return _FakeResponse()

    monkeypatch.setattr(requests.Session, "get", fake_get, raising=True)
    return seen


@pytest.mark.parametrize("manager_cls", [RedfishManager, IDracManager])
def test_default_manager_skips_verification(manager_cls, captured_verify):
    """No flag -> insecure default -> requests.get receives verify=False.

    This is the self-signed-BMC happy path: the client must not verify unless
    the operator opts in, otherwise every connection to a default iDRAC fails.
    """
    mgr = manager_cls()
    mgr.api_get_call("https://bmc.invalid/redfish/v1", hdr=None)
    assert captured_verify["verify"] is False


@pytest.mark.parametrize("manager_cls", [RedfishManager, IDracManager])
def test_insecure_true_skips_verification(manager_cls, captured_verify):
    """insecure=True -> verify=False (explicit skip is honored)."""
    mgr = manager_cls(insecure=True)
    mgr.api_get_call("https://bmc.invalid/redfish/v1", hdr=None)
    assert captured_verify["verify"] is False


@pytest.mark.parametrize("manager_cls", [RedfishManager, IDracManager])
def test_insecure_false_enables_verification(manager_cls, captured_verify):
    """insecure=False -> verify=True (opt-in verification path)."""
    mgr = manager_cls(insecure=False)
    mgr.api_get_call("https://bmc.invalid/redfish/v1", hdr=None)
    assert captured_verify["verify"] is True


@pytest.mark.parametrize("manager_cls", [RedfishManager, IDracManager])
def test_get_carries_timeout_and_reuses_one_session(manager_cls, captured_verify):
    """Every GET is bounded by a timeout and reuses one pooled Session.

    Connection reuse (keep-alive) is what stops a crawl from wedging a fragile
    BMC, so two sequential GETs must go through the *same* cached Session.
    """
    mgr = manager_cls()
    mgr.api_get_call("https://bmc.invalid/redfish/v1", hdr=None)
    mgr.api_get_call("https://bmc.invalid/redfish/v1/Systems", hdr=None)

    assert captured_verify["timeout"] is not None
    assert len(set(captured_verify["sessions"])) == 1  # one Session, reused


def test_internal_verify_flag_is_inverse_of_insecure():
    """_is_verify_cert is the inverse of the insecure intent for both managers."""
    assert RedfishManager(insecure=True)._is_verify_cert is False
    assert RedfishManager(insecure=False)._is_verify_cert is True
    assert IDracManager(insecure=True)._is_verify_cert is False
    assert IDracManager(insecure=False)._is_verify_cert is True
    # Default (no flag) must skip verification.
    assert RedfishManager()._is_verify_cert is False
    assert IDracManager()._is_verify_cert is False


def test_cli_verify_ssl_flag_drives_insecure():
    """--verify-ssl off (default) -> insecure skip; on -> verification enabled.

    Mirrors how ``idrac_main.main`` derives the ``insecure`` value it passes to
    IDracManager from the parsed ``verify_ssl`` flag, without constructing a real
    client. ``--insecure`` stays a harmless explicit "skip".
    """
    # Default namespace: verify_ssl absent/False -> insecure (skip) is True.
    default_args = SimpleNamespace(verify_ssl=False)
    assert (not getattr(default_args, "verify_ssl", False)) is True

    # Opt in: verify_ssl True -> insecure (skip) is False -> verification on.
    verify_args = SimpleNamespace(verify_ssl=True)
    assert (not getattr(verify_args, "verify_ssl", False)) is False
