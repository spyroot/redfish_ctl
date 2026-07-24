"""Constructor-neutralization tests for the manager connection contract.

One credential parameter set exists on the managers — ``host``/``username``/
``password``/``port`` stored once as ``_host``/``_username``/``_password``/
``_port`` — and the legacy ``redfish_*``/``idrac_*`` spellings survive only as
one-wave constructor aliases (and at the CLI/env edge). These tests pin the
canonical form, the alias coalescing, the Singleton-key stability across the
two spellings, and the request-count budget of vendor-profile resolution
(zero BMC round trips of its own).
Root-module test: flat placement mirrors redfish_ctl/redfish_manager.py.

Author Mus spyroot@gmail.com
"""
import inspect

from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import Singleton
from redfish_ctl.redfish_manager import RedfishManager


def test_canonical_construction_stores_one_set():
    """Canonical kwargs land in the one canonical storage set."""
    mgr = RedfishManager(host="1.2.3.4", username="u", password="p", port=444)
    assert mgr._host == "1.2.3.4"
    assert mgr._username == "u"
    assert mgr._password == "p"
    assert mgr._port == 444
    assert mgr.redfish_ip == "1.2.3.4:444"
    # Single storage: no parallel private attr survives on the instance.
    assert "_redfish_ip" not in vars(mgr)


def test_legacy_redfish_aliases_coalesce():
    """``redfish_*`` constructor spellings still construct, canonically stored."""
    mgr = RedfishManager(redfish_ip="10.0.0.1", redfish_username="a",
                         redfish_password="b", redfish_port="8443")
    assert mgr._host == "10.0.0.1"
    assert mgr._username == "a"
    assert mgr._password == "b"
    assert mgr._port == 8443


def test_legacy_idrac_aliases_coalesce():
    """``idrac_*`` constructor spellings still construct, canonically stored."""
    mgr = IDracManager(idrac_ip="10.0.0.2", idrac_username="c",
                       idrac_password="d", idrac_port=8080)
    assert mgr._host == "10.0.0.2"
    assert mgr._username == "c"
    assert mgr._password == "d"
    assert mgr._port == 8080


def test_canonical_spelling_wins_over_alias():
    """On a conflict the canonical keyword outranks the legacy alias."""
    mgr = RedfishManager(host="canonical", redfish_ip="legacy")
    assert mgr._host == "canonical"


def test_signatures_hold_exactly_one_credential_set():
    """The triple-password kill: no ``idrac_*``/``redfish_*`` parameters remain.

    The same secret used to exist as ``password``/``redfish_password``/
    ``idrac_password`` across the two constructors; after the cleanup the
    aliases are not parameters anywhere — they live only at the CLI/env edge.
    """
    for cls in (RedfishManager, IDracManager):
        names = set(inspect.signature(cls.__init__).parameters)
        assert {"host", "username", "password", "port"} <= names
        leaked = {n for n in names
                  if n.startswith("idrac_") or n.startswith("redfish_")}
        assert not leaked, f"{cls.__name__} still declares {leaked}"


def test_singleton_key_stable_across_spellings():
    """Canonical vs legacy kwargs for ONE connection yield ONE instance.

    The Singleton fingerprints the connection from either spelling; a key
    split here would double per-BMC caches mid-migration.
    """
    class _Probe(IDracManager, metaclass=Singleton):
        """Throwaway command-shaped class keyed like a real command."""

        def execute(self, **kwargs):
            """Satisfy the abstract command surface; never called here."""
            raise AssertionError("not invoked")

        @staticmethod
        def register_subcommand(cls):
            """Satisfy the abstract command surface; never called here."""
            raise AssertionError("not invoked")

    legacy = _Probe(idrac_ip="h1", idrac_username="u", idrac_password="p")
    canonical = _Probe(host="h1", username="u", password="p")
    assert legacy is canonical


def test_profile_resolution_spends_zero_requests():
    """Vendor-profile resolution performs no BMC I/O of its own.

    Budget rule: resolution rides evidence that already exists (a cached
    classification or an already-fetched ServiceRoot) or falls back to the
    class default — never a GET of its own. The bound here is 0, stronger
    than the design's <=1.
    """
    mgr = IDracManager(host="192.0.2.9", username="u", password="p")
    assert mgr.query_counter == 0
    profile = mgr.vendor_profile
    assert type(profile).vendor == "dell"
    assert mgr.query_counter == 0
    assert "_service_root" not in vars(mgr)

    probed = IDracManager(host="192.0.2.10", username="u", password="p")
    probed.__dict__["_service_root"] = {"Vendor": "Supermicro"}
    assert type(probed.vendor_profile).vendor == "supermicro"
    assert probed.query_counter == 0


def test_private_host_alias_writes_through():
    """The transitional ``_redfish_ip`` view reads and writes ``_host``."""
    mgr = RedfishManager(host="a", username="u", password="p")
    mgr._redfish_ip = "9.9.9.9"
    assert mgr._host == "9.9.9.9"
    assert mgr.redfish_ip == "9.9.9.9"
    assert mgr._redfish_ip == "9.9.9.9"


def test_unknown_kwargs_are_tolerated():
    """The constructor keeps ``**kwargs`` extensibility (the spec contract)."""
    mgr = RedfishManager(host="h", username="u", password="p",
                         future_extension_knob=True)
    assert mgr._host == "h"
