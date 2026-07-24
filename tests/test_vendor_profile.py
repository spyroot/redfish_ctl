"""Unit tests for the vendor profiles (redfish_ctl/vendor_profile.py).

Covers registration (loud collisions), observable generic fallback,
per-connection caching with test-isolation reset, ServiceRoot detection over
the REAL committed corpora (resolved through the manifest chain — no hardcoded
fixture paths), the per-lens chokepoint matrix (status decode, error decode,
task-id parsing — only the dell lens ever sees 201=Created or a ``JID_``
scrape), and the manager resolution ladder (evidence outranks the class
default; resolution never spends a BMC round trip).
Root-module test: flat placement mirrors redfish_ctl/vendor_profile.py.

Author Mus spyroot@gmail.com
"""
import json
from pathlib import Path

import pytest
from vendor_corpus import corpus_dir as extract_corpus

from redfish_ctl import dell_profile, vendor_profile
from redfish_ctl.cmd_exceptions import AuthenticationFailed, ResourceNotFound
from redfish_ctl.redfish_exceptions import (
    ProfileRegistrationCollision,
    RedfishUnauthorized,
)
from tools import corpus, corpus_diff

_TESTS_DIR = Path(__file__).resolve().parent

# The per-lens ServiceRoot fixtures the offline mock serves; each must
# classify to its own vendor so profile resolution works offline per lens.
_LENS_ROOTS = {
    "dell": _TESTS_DIR / "idrac_fixtures" / "_redfish_v1.json",
    "supermicro": _TESTS_DIR / "supermicro_fixtures" / "_redfish_v1.json",
    "hpe": _TESTS_DIR / "hpe_fixtures" / "_redfish_v1.json",
    "generic": _TESTS_DIR / "generic_fixtures" / "_redfish_v1.json",
}


class _FakeResponse:
    """Minimal stand-in response: status, headers, and an optional JSON body."""

    def __init__(self, status_code, headers=None, body=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body if body is not None else {}

    def json(self):
        """Return the configured JSON body.

        :return: the body dict this fake was built with.
        """
        return self._body


@pytest.fixture(autouse=True)
def _clean_profile_state():
    """Isolate every test from the module-level cache and counters."""
    vendor_profile.clear_profile_cache()
    yield
    vendor_profile.clear_profile_cache()


def test_known_vendors_resolve_to_their_profile_classes():
    """Each registered vendor key resolves to its own profile class.

    The registry is the whole dispatch surface — a wrong mapping here would
    hand a vendor another vendor's chokepoints, the exact leak this design
    exists to stop.
    """
    assert type(vendor_profile.resolve_profile("dell")) is dell_profile.DellProfile
    assert type(vendor_profile.resolve_profile("supermicro")) is vendor_profile.SupermicroProfile
    assert type(vendor_profile.resolve_profile("hpe")) is vendor_profile.HpeProfile
    assert type(vendor_profile.resolve_profile("generic")) is vendor_profile.DmtfProfile
    assert not vendor_profile.FALLBACK_COUNTS


def test_unknown_vendor_falls_back_to_generic_observably():
    """An unknown vendor yields the generic profile AND counts the fallback.

    Silent wrong-profile resolution is the design's worst failure mode; the
    fallback must be visible (counter + log), never quiet.
    """
    profile = vendor_profile.resolve_profile("acme")
    assert type(profile) is vendor_profile.DmtfProfile
    assert vendor_profile.FALLBACK_COUNTS == {"acme": 1}
    vendor_profile.resolve_profile("acme")
    assert vendor_profile.FALLBACK_COUNTS == {"acme": 2}


def test_duplicate_registration_collides_loudly():
    """Registering a DIFFERENT class under a taken key raises at once.

    Last-write-wins registration is how the flat command registry silently
    drops classes (architecture dispatch_note); profiles refuse it up front.
    Re-registering the SAME class stays idempotent (module re-import safety).
    """
    class Impostor(vendor_profile.DmtfProfile):
        """A second class claiming an already-registered vendor key."""
        vendor = "dell"

    with pytest.raises(ProfileRegistrationCollision, match="dell"):
        vendor_profile.register_profile("dell", Impostor)
    vendor_profile.register_profile("dell", dell_profile.DellProfile)  # idempotent


def test_connection_cache_probes_once_and_resets():
    """One probe per (host, port); the reset hook clears it for test isolation."""
    calls = []

    def loader():
        """Count ServiceRoot loads; serve a Supermicro-shaped root."""
        calls.append(1)
        return {"Vendor": "Supermicro"}

    p1 = vendor_profile.profile_for_connection("10.0.0.1", 443, loader)
    p2 = vendor_profile.profile_for_connection("10.0.0.1", 443, loader)
    assert p1 is p2 and len(calls) == 1
    assert type(p1) is vendor_profile.SupermicroProfile
    vendor_profile.clear_profile_cache()
    vendor_profile.profile_for_connection("10.0.0.1", 443, loader)
    assert len(calls) == 2


def test_explicit_vendor_override_skips_the_probe():
    """--vendor/REDFISH_VENDOR resolves with ZERO ServiceRoot loads.

    The operator's declaration outranks the probe (and saves the round trip —
    the avoid-BMC-round-trips rule).
    """
    def loader():
        """Fail the test if the probe fires despite an override."""
        raise AssertionError("probe must not fire when vendor is declared")

    profile = vendor_profile.profile_for_connection(
        "10.0.0.2", 443, loader, vendor_override="dell")
    assert type(profile) is dell_profile.DellProfile


@pytest.mark.parametrize("row", corpus.load_manifest(),
                         ids=lambda r: f"{r['vendor']}-{r['model']}")
def test_detection_over_every_committed_corpus(row):
    """Each real corpus's ServiceRoot resolves to a sane vendor profile.

    Evidence resolved mechanically (manifest -> tarball -> flattened root);
    the hard assertion is the anti-leak one: NO non-Dell box may ever resolve
    to the Dell profile (that is the mis-vendoring this program kills). The
    positive mapping is asserted where the classifier models the vendor;
    nvidia (not yet a classifier vocabulary word) must simply be non-Dell.
    """
    tarball = corpus._tarball_path(row)
    if not tarball.exists() or corpus._is_lfs_pointer(tarball):
        pytest.skip(f"{row['tarball']} not pulled (bare LFS pointer)")
    fetch = corpus_diff.corpus_fetcher(extract_corpus(tarball, row["arcname"]))
    root = fetch("/redfish/v1")
    assert isinstance(root, dict), "corpus lacks a ServiceRoot fixture"
    profile = vendor_profile.profile_for_service_root(root)
    if row["vendor"] == "dell":
        assert type(profile) is dell_profile.DellProfile
    else:
        assert type(profile) is not dell_profile.DellProfile, (
            f"non-Dell corpus {row['vendor']}/{row['model']} resolved the "
            "Dell profile — vendor semantics would leak")
    if row["vendor"] in ("supermicro", "hpe"):
        assert profile.vendor == row["vendor"]


@pytest.mark.parametrize("vendor", ["dell", "supermicro", "hpe", "generic"])
def test_decode_status_matrix(vendor):
    """Per lens: the shared 2xx folds hold, and ONLY dell maps 201=Created.

    201=Created is a Dell addition (architecture state_decode); a Created
    surfacing on any other lens means Dell semantics leaked into the shared
    base — the exact defect this design exists to stop.
    """
    profile = vendor_profile.resolve_profile(vendor)
    assert profile.decode_status(200).name == "Ok"
    assert profile.decode_status(202).name == "AcceptedTaskGenerated"
    assert profile.decode_status(204).name == "Success"
    assert profile.decode_status(299).name == "Success"
    assert profile.decode_status(404).name == "Error"
    if vendor == "dell":
        assert profile.decode_status(201).name == "Created"
    else:
        assert profile.decode_status(201).name == "Success"
    # The neutral map itself must hold no Created row.
    assert 201 not in vendor_profile.DmtfProfile._status_map


@pytest.mark.parametrize("vendor", ["dell", "supermicro", "hpe", "generic"])
def test_error_handler_matrix(vendor):
    """Per lens: 401 raises the lens's own exception family with its envelope.

    Dell raises ``AuthenticationFailed``/``UnexpectedResponse`` carrying the
    parsed IDRAC envelope; the DMTF lenses raise ``RedfishUnauthorized``.
    A cross-family raise means the wrong error handler ran for the box.
    """
    profile = vendor_profile.resolve_profile(vendor)
    body = {"error": {"code": "Base.1.18.GeneralError",
                      "message": "denied", "@Message.ExtendedInfo": []}}
    expected = AuthenticationFailed if vendor == "dell" else RedfishUnauthorized
    with pytest.raises(expected):
        profile.error_handler(_FakeResponse(401, body=body))
    with pytest.raises(ResourceNotFound):
        profile.error_handler(_FakeResponse(404, body=body))
    assert profile.error_handler(_FakeResponse(204)).name == "Success"


def test_dell_error_handler_records_envelope_on_manager():
    """The Dell decode records the parsed envelope as ``manager._redfish_error``.

    Callers read that attribute after a failed write; losing it during the
    relocation would silently break the error-envelope contract.
    """
    class _Carrier:
        """Bare attribute carrier standing in for a manager."""
        _redfish_error = None

    carrier = _Carrier()
    body = {"error": {"code": "IDRAC.2.9.SYS446", "message": "nope"}}
    with pytest.raises(ResourceNotFound):
        dell_profile.DellProfile.instance().error_handler(
            _FakeResponse(404, body=body), manager=carrier)
    assert carrier._redfish_error is not None
    assert carrier._redfish_error.status_code == 404


@pytest.mark.parametrize("vendor", ["dell", "supermicro", "hpe", "generic"])
def test_parse_task_id_matrix(vendor):
    """Per lens: Location header wins everywhere; only dell scrapes ``JID_``.

    A response with no Location but a ``JID_`` in its body yields the job id
    on the dell lens ONLY — a non-dell lens returning a ``JID_`` would mean
    the Dell body-scrape leaked back into the shared path.
    """
    profile = vendor_profile.resolve_profile(vendor)

    with_header = _FakeResponse(
        202, headers={"Location": "/redfish/v1/TaskService/Tasks/42"})
    assert profile.parse_task_id(with_header) == "42"

    body_only = _FakeResponse(202)
    body_only.text = '{"Id": "JID_000000000001", "queued": true}'
    got = profile.parse_task_id(body_only)
    if vendor == "dell":
        assert got and got.startswith("JID_")
    else:
        assert not got
        assert not str(got).startswith("JID_")


@pytest.mark.parametrize("vendor", ["dell", "supermicro", "hpe", "generic"])
def test_lens_service_roots_classify_to_their_vendor(vendor):
    """Each offline lens root resolves to its own profile (mock lens contract).

    The mock serves these roots at ``/redfish/v1``; if one stops classifying,
    offline per-lens resolution silently degrades to the generic profile.
    """
    root = json.loads(_LENS_ROOTS[vendor].read_text())
    profile = vendor_profile.profile_for_service_root(root)
    if vendor == "generic":
        assert type(profile) is vendor_profile.DmtfProfile
    else:
        assert profile.vendor == vendor


@pytest.mark.parametrize("vendor", ["dell", "supermicro", "hpe", "generic"])
def test_manager_chokepoint_delegation_per_lens(vendor):
    """Through the MANAGER delegate: the box's lens picks the decode, not class.

    Every command still declares IDracManager; with the lens's ServiceRoot
    bound to the connection, ``self.default_error_handler`` must resolve the
    box's semantics — 201 folds to Created ONLY when the box is Dell. This is
    the delegation audit that catches a Dell override silently re-shadowing
    the chokepoint.
    """
    from redfish_ctl.idrac_manager import IDracManager
    mgr = IDracManager(host=f"matrix-{vendor}", username="u", password="p")
    mgr.__dict__["_service_root"] = json.loads(_LENS_ROOTS[vendor].read_text())
    assert type(mgr.vendor_profile).vendor == vendor
    decoded = mgr.default_error_handler(_FakeResponse(201))
    assert decoded.name == ("Created" if vendor == "dell" else "Success")


def test_manager_resolution_ladder_defaults():
    """No evidence -> the class default: Dell manager presumes dell, base generic.

    Evidence always outranks the presumption: once a root is bound, a
    Supermicro box resolves supermicro even through the Dell manager class,
    and the classification is shared with other managers on the connection.
    """
    from redfish_ctl.idrac_manager import IDracManager
    from redfish_ctl.redfish_manager import RedfishManager
    dell_mgr = IDracManager(host="ladder-a", username="u", password="p")
    assert type(dell_mgr.vendor_profile) is dell_profile.DellProfile
    base_mgr = RedfishManager(host="ladder-b", username="u", password="p")
    assert type(base_mgr.vendor_profile) is vendor_profile.DmtfProfile

    probed = IDracManager(host="ladder-c", username="u", password="p")
    probed.__dict__["_service_root"] = {"Vendor": "Supermicro"}
    assert type(probed.vendor_profile) is vendor_profile.SupermicroProfile
    # The classification is connection-level: a second manager on the same
    # (host, port) shares it without holding a root of its own.
    sibling = IDracManager(host="ladder-c", username="u", password="p")
    assert type(sibling.vendor_profile) is vendor_profile.SupermicroProfile


def test_manager_property_is_present_and_lazy():
    """RedfishManager exposes vendor_profile as a property; nothing eager.

    Resolution happens per access with no import-graph change and no I/O of
    its own — presence and property-ness stay the contract.
    """
    from redfish_ctl.redfish_manager import RedfishManager
    assert isinstance(
        vars(RedfishManager).get("vendor_profile"), property)
