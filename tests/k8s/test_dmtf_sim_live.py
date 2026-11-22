"""Live consumer tests against the deployed DMTF simulator (private CI only).

This test is marked ``dmtf_sim_live`` and FAILS CLOSED through the
``dmtf_sim_endpoint`` fixture (which raises, never skips, when REDFISH_IP /
REDFISH_PORT are unset); the offline suite deselects it with
``-m 'not dmtf_sim_live'``. It runs only in private CI, where the sim is
deployed and the endpoint is injected. The sim serves DMTF-generic content, so
the product-neutral ``RedfishManager`` lens is the one under test — no vendor
(Dell/iDRAC/Supermicro) assumption is made. The endpoint is host + port +
``is_http``; no second environment variable or full URL is passed to
``redfish_ctl``.

Author Mus spyroot@gmail.com
"""
import pytest

from redfish_ctl.redfish_manager import RedfishManager

pytestmark = pytest.mark.dmtf_sim_live


def _manager(endpoint):
    """Create the generic manager for the one provider-resolved endpoint.

    :param endpoint: the fail-closed persistent-simulator endpoint fixture.
    :return: a product-neutral Redfish manager.
    """
    return RedfishManager(
        host=endpoint.host,
        port=endpoint.port,
        insecure=True,
        is_http=endpoint.is_http,
    )


def test_dmtf_service_root(dmtf_sim_endpoint):
    """The sim answers the Redfish service root over the one endpoint.

    Uses the generic manager so readiness proves the same HTTP stack that
    commands use, rather than a separate raw HTTP client.

    :param dmtf_sim_endpoint: the fail-closed persistent-sim endpoint fixture.
    :return: None.
    """
    result = _manager(dmtf_sim_endpoint).base_query("/redfish/v1/")
    assert result.error is None
    assert isinstance(result.data, dict)
    assert result.data.get("@odata.id") == "/redfish/v1/"
