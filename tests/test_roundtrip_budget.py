"""Round-trip budgets: pin how many BMC requests a sequence may cost.

A BMC request is expensive — an operator can easily sit hundreds of
milliseconds from the BMC (for example managing a US rack over VPN from
India at ~300ms per round trip), and a BMC's own processing is slow even
in-rack. The mock records every request, so instead of sleeping, these
tests PROJECT wall time as ``requests x RTT`` for named latency profiles
and assert the result against a budget. A regression that adds redundant
fetches fails here with the exact request count, the same way the AST
guard catches unsafe enum checks.

First offender pinned: the ServiceRoot identity properties
(``redfish_version``/``redfish_vendor``/``redfish_system``) each fetched
``/redfish/v1/`` separately — three round trips for one document, ~0.9s
of pure waste per command warm-up at the 300ms profile.

Author Mus spyroot@gmail.com
"""
from redfish_ctl.redfish_manager_shared import ApiRequestType

# Named client->BMC latency profiles (seconds per round trip). Projection
# only — tests never sleep, so the suite stays fast at any profile.
RTT_PROFILES = {
    "rack-local": 0.002,
    "office-vpn": 0.080,
    "india-vpn-to-us": 0.300,
    "congested-vpn": 0.800,
}


def _gets(service, path=None):
    """GET requests recorded by the mock, optionally for one exact path."""
    return [
        r for r in service.requests
        if r.method == "GET"
        and (path is None or r.path.rstrip("/") == path.rstrip("/"))
    ]


def projected_walltime(service, profile: str) -> float:
    """Serial-client wall time for the recorded sequence under a profile."""
    return len(service.requests) * RTT_PROFILES[profile]


def test_service_root_is_fetched_once(redfish_mock, redfish_service):
    """The three identity properties share ONE ServiceRoot fetch.

    Each property is a cached_property backed by ``/redfish/v1/``; before
    the fix each did its own GET of the same document (3 round trips).
    """
    _ = redfish_mock.redfish_version
    _ = redfish_mock.redfish_vendor
    _ = redfish_mock.redfish_system

    assert len(_gets(redfish_service, "/redfish/v1")) == 1


def test_identity_warmup_fits_india_vpn_budget(redfish_mock, redfish_service):
    """Connection warm-up affords ONE round trip even at 300ms RTT.

    At the india-vpn-to-us profile the pre-fix triple fetch projected to
    ~0.9s before any real work; the budget pins it to a single trip.
    """
    _ = redfish_mock.redfish_version
    _ = redfish_mock.redfish_vendor
    _ = redfish_mock.redfish_system

    assert projected_walltime(redfish_service, "india-vpn-to-us") <= 0.301


def test_system_command_round_trip_budget(redfish_mock, redfish_service):
    """The system command's full sequence stays within its request budget.

    The budget is the measured cost of the current implementation; any
    change that adds requests to this hot path must either come in under
    budget or consciously raise it in review. Projections keep the cost
    readable in operator terms at each latency profile.
    """
    redfish_mock.sync_invoke(ApiRequestType.SystemQuery, "system_query")

    n_requests = len(redfish_service.requests)
    assert n_requests <= 6, (
        f"system command grew to {n_requests} BMC round trips; "
        f"that is {n_requests * RTT_PROFILES['india-vpn-to-us']:.1f}s at 300ms RTT — "
        "avoid the extra fetch or consciously raise this budget in review"
    )
    assert projected_walltime(redfish_service, "india-vpn-to-us") <= 2.0
