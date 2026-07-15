"""Unit tests for idrac_ctl.redfish_query.RedfishQuery (offline).

Author Mus spyroot@gmail.com
"""
import pytest

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.redfish_manager_shared import ApiRequestType
from redfish_ctl.redfish_main import (
    _redfish_query_from_args,
    _validate_redfish_query_for_vendor,
)
from redfish_ctl.redfish_query import RedfishQuery
from redfish_ctl.vendors import get_vendor


class QueryArgs:
    """Small argparse-like object for global Redfish query flags."""

    query_select = "Id,Name"
    query_filter = "Id eq 'Manager'"
    query_expand = "."
    query_expand_levels = 2
    query_top = 5
    query_only = True


def test_empty_query_is_blank():
    """No parameters -> empty string (caller appends nothing)."""
    q = RedfishQuery()
    assert q.is_empty()
    assert q.to_query_string() == ""


def test_global_cli_query_flags_build_redfish_query():
    """Global CLI-style query args become one RedfishQuery object."""
    query = _redfish_query_from_args(QueryArgs())

    assert query.to_query_string() == (
        "?$select=Id,Name&$filter=Id%20eq%20'Manager'"
        "&$expand=.($levels=2)&$top=5&only"
    )


def test_vendor_gate_rejects_unsupported_query_flags():
    """Vendor profiles block query parameters not declared for that target."""
    query = RedfishQuery(select="Id")

    with pytest.raises(InvalidArgument, match=r"\$select"):
        _validate_redfish_query_for_vendor(query, get_vendor("supermicro"))


def test_vendor_gate_treats_top_zero_as_active_query():
    """$top=0 is valid Redfish syntax and still needs vendor support."""
    query = RedfishQuery(top=0)

    with pytest.raises(InvalidArgument, match=r"\$top"):
        _validate_redfish_query_for_vendor(query, get_vendor("supermicro"))


def test_dell_vendor_gate_rejects_combined_query_flags():
    """Dell's one-query-parameter rule is enforced before any GET."""
    query = RedfishQuery(select="Id", top=5)

    with pytest.raises(InvalidArgument, match="only one query parameter"):
        _validate_redfish_query_for_vendor(query, get_vendor("dell"))


def test_select_list_and_string():
    """$select accepts a list or a comma string and renders the same."""
    assert RedfishQuery(select=["A", "B"]).to_query_string() == "?$select=A,B"
    assert RedfishQuery(select="A,B").to_query_string() == "?$select=A,B"


def test_top_renders_int():
    """$top renders the integer value."""
    assert RedfishQuery(top=5).to_query_string() == "?$top=5"
    assert RedfishQuery(top=0).to_query_string() == "?$top=0"


def test_expand_with_levels():
    """$expand renders the Redfish levels syntax; mode defaults to '*'."""
    assert RedfishQuery(expand=True, expand_levels=2).to_query_string() == "?$expand=*($levels=2)"
    assert RedfishQuery(expand=".").to_query_string() == "?$expand=.($levels=1)"


def test_only_flag():
    """only is a valueless query parameter (no leading $)."""
    assert RedfishQuery(only=True).to_query_string() == "?only"


def test_filter_is_url_encoded_but_keeps_operators():
    """$filter encodes spaces but keeps Redfish operators/quotes literal."""
    q = RedfishQuery(filter="Id eq '10191'")
    assert q.to_query_string() == "?$filter=Id%20eq%20'10191'"


def test_apply_appends_to_url():
    """apply() appends the query string to a URL."""
    url = "https://idrac/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Lclog/Entries"
    assert RedfishQuery(top=5).apply(url) == url + "?$top=5"


def test_one_param_per_uri_rejects_combining():
    """With the Dell one-param-per-URI rule, combining parameters raises."""
    q = RedfishQuery(select=["A"], top=5)
    with pytest.raises(ValueError):
        q.to_query_string(one_param_per_uri=True)
    # but each alone is fine
    assert RedfishQuery(top=5).to_query_string(one_param_per_uri=True) == "?$top=5"


def test_multiple_params_allowed_for_generic():
    """Without the restriction, parameters combine with '&'."""
    out = RedfishQuery(select=["A"], top=5).to_query_string()
    assert out.startswith("?")
    assert "$select=A" in out and "$top=5" in out and "&" in out


def test_negative_top_rejected():
    """$top must be >= 0."""
    with pytest.raises(ValueError):
        RedfishQuery(top=-1).to_query_string()


def test_bad_expand_levels_rejected():
    """$expand levels must be >= 1."""
    with pytest.raises(ValueError):
        RedfishQuery(expand=True, expand_levels=0).to_query_string()


# --- transport integration: get_with_query applies params, offline ----------

def test_get_with_query_applies_params(redfish_mock, redfish_service):
    """get_with_query appends the query string and the server receives it."""
    resp = redfish_mock.get_with_query(
        "https://mock-idrac/redfish/v1/Managers", RedfishQuery(top=5)
    )
    assert resp.status_code == 200
    assert "top=5" in redfish_service.last_request.query  # lowercased by requests-mock


def test_get_with_query_none_is_plain_get(redfish_mock):
    """No query yields a plain GET that still resolves the fixture."""
    resp = redfish_mock.get_with_query(
        "https://mock-idrac/redfish/v1/Managers", None
    )
    assert resp.status_code == 200
    assert resp.json()["@odata.id"] == "/redfish/v1/Managers"


def test_get_with_query_enforces_one_param(redfish_mock):
    """With the Dell one-param-per-URI rule, combining params raises before the call."""
    q = RedfishQuery(select=["Id"], top=5)
    with pytest.raises(ValueError):
        redfish_mock.get_with_query(
            "https://mock-idrac/redfish/v1/Managers", q, one_param_per_uri=True
        )


def test_invoke_attaches_global_query_to_base_query(redfish_mock, redfish_service):
    """Dispatch stores global query flags so base_query appends them to GETs."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.QueryIdrac,
        "query_idrac",
        resource="/redfish/v1/Managers",
        redfish_query=RedfishQuery(top=1),
        redfish_query_one_param_per_uri=False,
    )

    assert result.data["@odata.id"] == "/redfish/v1/Managers"
    assert "top=1" in redfish_service.last_request.query
