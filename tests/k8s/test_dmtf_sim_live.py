"""Live consumer tests against the deployed DMTF simulator (private CI only).

These are marked ``dmtf_sim_live``: they FAIL CLOSED through the
``dmtf_sim_endpoint`` fixture (which raises, never skips, when REDFISH_IP /
REDFISH_PORT are unset), and the offline suite deselects them with
``-m 'not dmtf_sim_live'``. They run only in private CI, where the sim is
deployed and the endpoint is injected. The sim serves DMTF-generic content, so
the product-neutral ``RedfishManager`` lens is the one under test — no vendor
(Dell/iDRAC/Supermicro) assumption is made. The endpoint is host + port +
``is_http`` (a URL is derived locally when a raw request needs one; no second
environment variable, no full URL passed to redfish_ctl).

    redfish_ctl --ip "$REDFISH_IP" --port "$REDFISH_PORT" --use-http metric-definitions

Author Mus spyroot@gmail.com
"""
import pytest
import requests

from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import RedfishManager

pytestmark = pytest.mark.dmtf_sim_live


def test_metric_definitions_from_persistent_sim(dmtf_sim_endpoint):
    """Invoking a telemetry command against the sim returns metric definitions.

    Proves the telemetry read/decode path works against DMTF-truth telemetry
    resources through the generic lens; the same invoke emits the metrics/spans
    the existing Splunk gates validate (Splunk cannot tell hardware from virtual).

    :param dmtf_sim_endpoint: the fail-closed persistent-sim endpoint fixture.
    :return: None.
    """
    manager = RedfishManager(
        host=dmtf_sim_endpoint.host,
        port=dmtf_sim_endpoint.port,
        username="root",
        password="mock",
        insecure=True,
        is_http=dmtf_sim_endpoint.is_http,
    )
    result = manager.sync_invoke(
        ApiRequestType.MetricReportDefinitions,
        "metric-definitions",
    )
    assert result.error is None
    assert result.data


def test_dmtf_service_root(dmtf_sim_endpoint):
    """The sim answers the Redfish service root over the one endpoint.

    Derives the URL locally from the canonical host + port — no second variable
    and no full endpoint URL is passed to redfish_ctl.

    :param dmtf_sim_endpoint: the fail-closed persistent-sim endpoint fixture.
    :return: None.
    """
    base_url = f"http://{dmtf_sim_endpoint.host}:{dmtf_sim_endpoint.port}"
    response = requests.get(f"{base_url}/redfish/v1/", timeout=10)
    assert response.status_code == 200
