"""Unit tests for the vendor-profile plumbing (redfish_ctl/vendor_profile.py).

Covers what the plumbing genuinely does today: loud registration collisions,
observable generic fallback, per-connection caching with test-isolation reset,
ServiceRoot detection over the REAL committed corpora (resolved through the
manifest chain — no hardcoded fixture paths), and the seam contract that
unimplemented chokepoints fail loudly instead of running half-moved semantics.
Root-module test: flat placement mirrors redfish_ctl/vendor_profile.py.

Author Mus spyroot@gmail.com
"""

import pytest
from vendor_corpus import corpus_dir as extract_corpus

from redfish_ctl import dell_profile, vendor_profile
from redfish_ctl.redfish_exceptions import ProfileRegistrationCollision
from tools import corpus, corpus_diff


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


def test_unimplemented_chokepoints_fail_loudly():
    """Every seam raises NotImplementedError until its body is relocated.

    A half-wired tree must crash with the relocation anchor in the message,
    never silently run partial semantics.
    """
    generic = vendor_profile.DmtfProfile.instance()
    dell = dell_profile.DellProfile.instance()
    for profile, method, args in [
        (generic, "decode_status", (200,)),
        (generic, "parse_task_id", (None,)),
        (dell, "decode_status", (201,)),
        (dell, "parse_task_id", (None,)),
        (dell, "fetch_task", (None, "JID_1")),
    ]:
        with pytest.raises(NotImplementedError, match="CHIP"):
            getattr(profile, method)(*args)


def test_manager_property_is_present_and_lazy():
    """RedfishManager exposes vendor_profile as a property; nothing eager.

    The plumbing must not change the import graph or any behavior until the
    delegation pass wires call sites — presence and property-ness is the
    whole contract today.
    """
    from redfish_ctl.redfish_manager import RedfishManager
    assert isinstance(
        vars(RedfishManager).get("vendor_profile"), property)
