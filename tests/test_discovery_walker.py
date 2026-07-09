"""Offline tests for the recursive Redfish discovery walker.

These exercise ``Discovery.recursive_discovery`` against a small synthetic
Redfish graph, with no live iDRAC and no network. The walker's HTTP fetch
(``base_query``) is replaced by an in-memory graph server that counts how many
times each logical resource is requested, so we can assert:

* URI variants of one resource (trailing slash, ``$expand``/``$ref`` query
  string, duplicate slashes) collapse to a single fetch;
* a reference cycle (A -> B -> A) terminates and fetches each node once;
* recursion stops once it passes the requested ``max_depth``.

The walker is driven on an instance built with ``__init__`` bypassed: it only
touches a handful of plain attributes plus ``base_query``, so a real network
client is never constructed. The whole module runs green with no IDRAC_IP set.

Author Mus spyroot@gmail.com
"""
import pytest
import requests

from idrac_ctl.discovery import cmd_discovery
from idrac_ctl.discovery.cmd_discovery import (
    DEFAULT_DISCOVERY_MAX_DEPTH,
    LOG_ENTRY_MEMBER_RE,
    Discovery,
)
from idrac_ctl.redfish_exceptions import RedfishForbidden, RedfishNotFound
from idrac_ctl.redfish_manager import CommandResult


class _FakeGraph:
    """In-memory Redfish service backed by a ``normalized path -> payload`` map.

    Stands in for ``Discovery.base_query``: the walker normalizes a path and
    calls this with the canonical form, so the map is keyed on canonical paths.
    Each call is tallied in :attr:`fetch_counts` so a test can prove a logical
    resource was fetched exactly once even when its links spell it many ways.
    A path absent from the graph raises ``RedfishNotFound`` to mimic a 404.
    """

    def __init__(self, graph):
        self.graph = graph
        self.fetch_counts = {}

    def base_query(self, resource_path, *args, **kwargs):
        self.fetch_counts[resource_path] = self.fetch_counts.get(resource_path, 0) + 1
        if resource_path not in self.graph:
            raise RedfishNotFound(resource_path)
        # extra="GET" -> the Allow header the walker records as allowed methods.
        return CommandResult(self.graph[resource_path], None, "GET", None)


def _make_discovery(tmp_path, graph, query_filter=None):
    """Build a Discovery whose ``base_query`` serves ``graph``, no network.

    ``__init__`` (which would construct a real Redfish client) is bypassed; we
    set only the attributes ``recursive_discovery`` actually reads. JSON dumps
    land in ``tmp_path``. Returns ``(discovery, fake_graph)``.
    """
    disc = Discovery.__new__(Discovery)
    disc.visited_urls = {}
    disc._discovered_url_file_mapping = {}
    disc._api_allowed_methods = {}
    disc.default_query_filter = list(query_filter or [])
    disc.json_response_dir = str(tmp_path)

    fake = _FakeGraph(graph)
    # Instance attribute shadows the bound method, so it is called with just
    # the resource path (no implicit self), matching base_query's real call.
    disc.base_query = fake.base_query
    return disc, fake


# --------------------------------------------------------------------------- #
# normalize_resource_path
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("/redfish/v1/Managers", "/redfish/v1/Managers"),
        ("/redfish/v1/Managers/", "/redfish/v1/Managers"),
        ("/redfish/v1/Managers///", "/redfish/v1/Managers"),
        ("/redfish/v1/Managers?$expand=*($levels=1)", "/redfish/v1/Managers"),
        ("/redfish/v1/Managers?$select=Name", "/redfish/v1/Managers"),
        ("/redfish/v1/Managers/?$ref", "/redfish/v1/Managers"),
        ("/redfish//v1//Managers", "/redfish/v1/Managers"),
        ("/redfish/v1/Managers#/Fragment", "/redfish/v1/Managers"),
        ("/redfish/v1/", "/redfish/v1"),
        ("/", "/"),
        ("", ""),
    ],
)
def test_normalize_resource_path_variants(raw, expected):
    """Every URI spelling of one resource collapses to a single canonical key."""
    assert Discovery.normalize_resource_path(raw) == expected


def test_normalize_collapses_variants_to_one_key():
    """The trailing-slash / query / dup-slash spellings all map to one value."""
    base = "/redfish/v1/Systems/1"
    variants = [
        base,
        base + "/",
        base + "?$expand=*",
        base + "/?$ref",
        "/redfish//v1/Systems/1",
        base + "#anchor",
    ]
    canonical = {Discovery.normalize_resource_path(v) for v in variants}
    assert canonical == {base}


# --------------------------------------------------------------------------- #
# Cycle + URI-variant dedup
# --------------------------------------------------------------------------- #

def test_cycle_and_variants_fetch_each_resource_once(tmp_path):
    """A -> B -> A with variant spellings fetches A and B exactly once each.

    A links to B under four spellings (plain, trailing slash, query string,
    duplicate slash) and B links back to A under two. With normalization and
    the visited guard, neither node is re-fetched and the walk terminates.
    """
    graph = {
        "/redfish/v1/A": {
            "@odata.id": "/redfish/v1/A",
            "Links": [
                {"@odata.id": "/redfish/v1/B"},
                {"@odata.id": "/redfish/v1/B/"},
                {"@odata.id": "/redfish/v1/B?$expand=*($levels=1)"},
                {"@odata.id": "/redfish//v1/B"},
            ],
        },
        "/redfish/v1/B": {
            "@odata.id": "/redfish/v1/B",
            "Back": {"@odata.id": "/redfish/v1/A"},
            "BackSlash": {"@odata.id": "/redfish/v1/A/"},
            "BackQuery": {"@odata.id": "/redfish/v1/A?$select=Name"},
        },
    }
    disc, fake = _make_discovery(tmp_path, graph)

    disc.recursive_discovery("/redfish/v1/A")

    assert fake.fetch_counts == {"/redfish/v1/A": 1, "/redfish/v1/B": 1}
    # Both logical resources recorded, keyed on the canonical path.
    assert set(disc._discovered_url_file_mapping) == {"/redfish/v1/A", "/redfish/v1/B"}


def test_entering_via_a_variant_still_dedupes(tmp_path):
    """Starting the walk on a trailing-slash/query variant still keys on canon."""
    graph = {
        "/redfish/v1/A": {
            "@odata.id": "/redfish/v1/A",
            "Next": {"@odata.id": "/redfish/v1/A?$expand=*"},  # self via variant
        },
    }
    disc, fake = _make_discovery(tmp_path, graph)

    disc.recursive_discovery("/redfish/v1/A/?$ref")

    assert fake.fetch_counts == {"/redfish/v1/A": 1}


# --------------------------------------------------------------------------- #
# Depth bound
# --------------------------------------------------------------------------- #

def _linear_chain(length):
    """Build R0 -> R1 -> ... -> R(length-1), each linking to the next node."""
    graph = {}
    for i in range(length):
        node = {"@odata.id": f"/redfish/v1/R{i}"}
        if i + 1 < length:
            node["Next"] = {"@odata.id": f"/redfish/v1/R{i + 1}"}
        graph[f"/redfish/v1/R{i}"] = node
    return graph


def test_recursion_stops_past_max_depth(tmp_path):
    """With max_depth=2, only R0 (d0), R1 (d1), R2 (d2) are fetched."""
    graph = _linear_chain(6)
    disc, fake = _make_discovery(tmp_path, graph)

    disc.recursive_discovery("/redfish/v1/R0", depth=0, max_depth=2)

    assert set(fake.fetch_counts) == {
        "/redfish/v1/R0",
        "/redfish/v1/R1",
        "/redfish/v1/R2",
    }
    assert all(count == 1 for count in fake.fetch_counts.values())
    # Anything deeper than the bound is never requested.
    for deeper in ("/redfish/v1/R3", "/redfish/v1/R4", "/redfish/v1/R5"):
        assert deeper not in fake.fetch_counts


def test_max_depth_zero_fetches_only_the_root(tmp_path):
    """A zero bound walks the entry resource and nothing below it."""
    graph = _linear_chain(3)
    disc, fake = _make_discovery(tmp_path, graph)

    disc.recursive_discovery("/redfish/v1/R0", depth=0, max_depth=0)

    assert set(fake.fetch_counts) == {"/redfish/v1/R0"}


def test_default_depth_walks_a_normal_tree(tmp_path):
    """Within the default bound the whole chain is discovered."""
    length = 8
    assert length <= DEFAULT_DISCOVERY_MAX_DEPTH
    graph = _linear_chain(length)
    disc, fake = _make_discovery(tmp_path, graph)

    disc.recursive_discovery("/redfish/v1/R0")

    assert len(fake.fetch_counts) == length
    assert all(count == 1 for count in fake.fetch_counts.values())


# --------------------------------------------------------------------------- #
# Filters and error handling
# --------------------------------------------------------------------------- #

def test_query_filter_skips_without_fetch(tmp_path):
    """A path matching default_query_filter is marked visited, never fetched."""
    graph = {
        "/redfish/v1/A": {
            "@odata.id": "/redfish/v1/A",
            "Log": {"@odata.id": "/redfish/v1/Managers/iDRAC/LogServices/Sel/Entries"},
        },
    }
    disc, fake = _make_discovery(
        tmp_path, graph, query_filter=["LogServices/Sel/Entries"]
    )

    disc.recursive_discovery("/redfish/v1/A")

    assert fake.fetch_counts == {"/redfish/v1/A": 1}
    assert "/redfish/v1/Managers/iDRAC/LogServices/Sel/Entries" in disc.visited_urls


# --------------------------------------------------------------------------- #
# Log-entry members skip (crawl safety) — LOG_ENTRY_MEMBER_RE
# --------------------------------------------------------------------------- #

_ENTRIES = "/redfish/v1/Managers/HGX_BMC_0/LogServices/Journal/Entries"


def _log_graph():
    """A -> Journal Entries collection -> two individual entry members."""
    return {
        "/redfish/v1/A": {
            "@odata.id": "/redfish/v1/A",
            "Log": {"@odata.id": _ENTRIES},
        },
        _ENTRIES: {
            "@odata.id": _ENTRIES,
            "Members": [
                {"@odata.id": _ENTRIES + "/abc_100"},
                {"@odata.id": _ENTRIES + "/def_200"},
            ],
        },
        _ENTRIES + "/abc_100": {"@odata.id": _ENTRIES + "/abc_100", "Message": "x"},
        _ENTRIES + "/def_200": {"@odata.id": _ENTRIES + "/def_200", "Message": "y"},
    }


def test_log_entry_members_skipped_by_default(tmp_path):
    """Default crawl keeps the Entries collection but skips its members.

    Enumerating every LogServices entry (thousands on a live GB300 HGX BMC)
    overloaded the BMC (SSL EOF) for no structural value, so members are marked
    visited and never fetched unless --include-log-entries is set. The
    ``_make_discovery`` helper leaves ``_skip_log_entries`` unset; the walker's
    ``getattr(..., True)`` default means skip, matching production.
    """
    disc, fake = _make_discovery(tmp_path, _log_graph())

    disc.recursive_discovery("/redfish/v1/A")

    # Root + the Entries collection index are fetched...
    assert fake.fetch_counts == {"/redfish/v1/A": 1, _ENTRIES: 1}
    # ...but the individual entry members are skipped (visited, never fetched).
    assert disc.visited_urls.get(_ENTRIES + "/abc_100") is True
    assert disc.visited_urls.get(_ENTRIES + "/def_200") is True
    assert _ENTRIES + "/abc_100" not in fake.fetch_counts
    assert _ENTRIES + "/abc_100" not in disc._discovered_url_file_mapping


def test_log_entry_members_crawled_when_included(tmp_path):
    """With include_log_entries (``_skip_log_entries=False``) members are fetched."""
    disc, fake = _make_discovery(tmp_path, _log_graph())
    disc._skip_log_entries = False

    disc.recursive_discovery("/redfish/v1/A")

    assert fake.fetch_counts.get(_ENTRIES + "/abc_100") == 1
    assert fake.fetch_counts.get(_ENTRIES + "/def_200") == 1
    assert _ENTRIES + "/abc_100" in disc._discovered_url_file_mapping


@pytest.mark.parametrize(
    "path, is_member",
    [
        ("/redfish/v1/Managers/HGX_BMC_0/LogServices/Journal/Entries/abc_1", True),
        ("/redfish/v1/Managers/iDRAC/LogServices/Sel/Entries/12", True),
        ("/redfish/v1/Managers/iDRAC/LogServices/Lclog/Entries/9", True),
        ("/redfish/v1/Systems/1/LogServices/EventLog/Entries/xyz", True),
        # The Entries collection index itself is NOT a member (kept).
        ("/redfish/v1/Managers/HGX_BMC_0/LogServices/Journal/Entries", False),
        # The LogService and the LogServices collection are kept.
        ("/redfish/v1/Managers/HGX_BMC_0/LogServices/Journal", False),
        ("/redfish/v1/Managers/HGX_BMC_0/LogServices", False),
        # A non-log resource that merely contains 'Entries' is kept.
        ("/redfish/v1/Systems/1/Memory/Entries", False),
    ],
)
def test_log_entry_member_regex(path, is_member):
    """LOG_ENTRY_MEMBER_RE matches individual entries across vendors, never the
    collection index or unrelated resources."""
    assert bool(LOG_ENTRY_MEMBER_RE.search(path)) is is_member


# --------------------------------------------------------------------------- #
# _query_with_retry — resilience to fragile-BMC transport drops
# --------------------------------------------------------------------------- #


def test_query_with_retry_recovers_from_transient_error(tmp_path, monkeypatch):
    """A transient transport drop (SSL EOF) is retried, not lost.

    GB300 HGX BMCs drop HTTPS connections under a sustained crawl; the walker
    must back off and retry rather than skip the resource.
    """
    monkeypatch.setattr(cmd_discovery.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setenv("IDRAC_DISCOVERY_RETRIES", "4")
    monkeypatch.setenv("IDRAC_DISCOVERY_BACKOFF", "0")
    monkeypatch.setenv("IDRAC_DISCOVERY_PACE_MS", "0")
    disc, _ = _make_discovery(tmp_path, {})

    calls = {"n": 0}

    def flaky(resource_path, *a, **k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.exceptions.ConnectionError("SSL UNEXPECTED_EOF")
        return CommandResult({"ok": True}, None, "GET", None)

    disc.base_query = flaky

    result = disc._query_with_retry("/redfish/v1/X")

    assert result.data == {"ok": True}
    assert calls["n"] == 3  # two failures + one success


def test_query_with_retry_raises_after_exhausting(tmp_path, monkeypatch):
    """When every attempt drops, the last transport error is raised."""
    monkeypatch.setattr(cmd_discovery.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setenv("IDRAC_DISCOVERY_RETRIES", "3")
    monkeypatch.setenv("IDRAC_DISCOVERY_BACKOFF", "0")
    disc, _ = _make_discovery(tmp_path, {})

    calls = {"n": 0}

    def always_fail(resource_path, *a, **k):
        calls["n"] += 1
        raise requests.exceptions.ConnectionError("down")

    disc.base_query = always_fail

    with pytest.raises(requests.exceptions.ConnectionError):
        disc._query_with_retry("/redfish/v1/X")
    assert calls["n"] == 3  # exactly IDRAC_DISCOVERY_RETRIES attempts


def test_query_with_retry_does_not_retry_non_transport(tmp_path, monkeypatch):
    """A non-transport error (e.g. 404) propagates at once, never retried.

    Only ``requests.RequestException`` transport drops are transient; a
    RedfishNotFound is terminal and must reach the caller's handler unchanged.
    """
    monkeypatch.setattr(cmd_discovery.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setenv("IDRAC_DISCOVERY_RETRIES", "4")
    disc, _ = _make_discovery(tmp_path, {})

    calls = {"n": 0}

    def not_found(resource_path, *a, **k):
        calls["n"] += 1
        raise RedfishNotFound("/redfish/v1/X")

    disc.base_query = not_found

    with pytest.raises(RedfishNotFound):
        disc._query_with_retry("/redfish/v1/X")
    assert calls["n"] == 1  # no retries for a terminal error


def test_non_redfish_path_is_ignored(tmp_path):
    """A link outside /redfish/v1 is neither fetched nor recorded."""
    graph = {
        "/redfish/v1/A": {
            "@odata.id": "/redfish/v1/A",
            "External": {"@odata.id": "/some/other/path"},
        },
    }
    disc, fake = _make_discovery(tmp_path, graph)

    disc.recursive_discovery("/redfish/v1/A")

    assert fake.fetch_counts == {"/redfish/v1/A": 1}
    assert "/some/other/path" not in disc.visited_urls


def test_forbidden_marks_visited_and_reports(tmp_path, capsys):
    """A 403 on a child is swallowed: marked visited, sibling walk continues."""
    graph = {
        "/redfish/v1/A": {
            "@odata.id": "/redfish/v1/A",
            "Denied": {"@odata.id": "/redfish/v1/Secret"},
            "Ok": {"@odata.id": "/redfish/v1/B"},
        },
        "/redfish/v1/B": {"@odata.id": "/redfish/v1/B"},
    }
    disc, fake = _make_discovery(tmp_path, graph)

    def base_query(resource_path, *args, **kwargs):
        fake.fetch_counts[resource_path] = fake.fetch_counts.get(resource_path, 0) + 1
        if resource_path == "/redfish/v1/Secret":
            raise RedfishForbidden("403 on Secret")
        return CommandResult(graph[resource_path], None, "GET", None)

    disc.base_query = base_query
    disc.recursive_discovery("/redfish/v1/A")

    assert disc.visited_urls.get("/redfish/v1/Secret") is True
    assert fake.fetch_counts["/redfish/v1/B"] == 1
    out = capsys.readouterr().out
    assert "Forbidden:" in out


def test_generic_error_is_not_mislabeled_forbidden(tmp_path, capsys):
    """A non-403 failure reports a generic discovery error, not "Forbidden:".

    Regression for the handler that printed "Forbidden:" for every Exception.
    """
    graph = {
        "/redfish/v1/A": {
            "@odata.id": "/redfish/v1/A",
            "Broken": {"@odata.id": "/redfish/v1/Boom"},
        },
    }
    disc, fake = _make_discovery(tmp_path, graph)

    def base_query(resource_path, *args, **kwargs):
        fake.fetch_counts[resource_path] = fake.fetch_counts.get(resource_path, 0) + 1
        if resource_path == "/redfish/v1/Boom":
            raise RuntimeError("connection reset")
        return CommandResult(graph[resource_path], None, "GET", None)

    disc.base_query = base_query
    disc.recursive_discovery("/redfish/v1/A")

    assert disc.visited_urls.get("/redfish/v1/Boom") is True
    out = capsys.readouterr().out
    assert "Discovery error at /redfish/v1/Boom" in out
    assert "Forbidden: connection reset" not in out


# --------------------------------------------------------------------------- #
# extract_odata_ids — non-string references (JsonSchemas $ref)
# --------------------------------------------------------------------------- #


def test_extract_odata_ids_skips_non_string_refs(tmp_path):
    """Only string @odata.id / Uri values are yielded.

    A JsonSchemas document defines the @odata.id property as a dict
    ({"$ref": ...}); a Uri can likewise be a non-string. These must be skipped,
    not yielded, or the downstream normalize_resource_path crashes with
    'dict object has no attribute split'. Seen live on Supermicro GB300.
    """
    disc, _ = _make_discovery(tmp_path, {})
    schema_like = {
        "@odata.id": "/redfish/v1/JsonSchemas/AccountService",  # real string ref
        "definitions": {
            "AccountService": {
                "properties": {
                    # schema *definition* of @odata.id — a dict, must be skipped
                    "@odata.id": {"$ref": "http://redfish.dmtf.org/schemas/v1/odata-v4.json#/definitions/id"},
                    "Uri": {"type": "object"},  # non-string Uri — must be skipped
                }
            }
        },
        "Members": [{"Uri": "/redfish/v1/Real/1"}],  # real string Uri
    }

    found = list(disc.extract_odata_ids(schema_like))

    assert all(isinstance(x, str) for x in found)
    assert "/redfish/v1/JsonSchemas/AccountService" in found
    assert "/redfish/v1/Real/1" in found


def test_recursive_discovery_survives_schema_doc_with_dict_odata_id(tmp_path):
    """recursive_discovery completes when a fetched doc embeds a dict @odata.id.

    Regression for the live GB300 crash: walking into a JsonSchemas payload
    whose @odata.id is a {"$ref": ...} dict must not raise.
    """
    graph = {
        "/redfish/v1/JsonSchemas/X": {
            "@odata.id": "/redfish/v1/JsonSchemas/X",
            "properties": {
                "@odata.id": {"$ref": "http://redfish.dmtf.org/schemas/v1/odata-v4.json#/definitions/id"},
            },
        },
    }
    disc, fake = _make_discovery(tmp_path, graph)

    # Must not raise AttributeError on the dict @odata.id.
    disc.recursive_discovery("/redfish/v1/JsonSchemas/X")

    assert disc.visited_urls.get("/redfish/v1/JsonSchemas/X") is True
    assert fake.fetch_counts.get("/redfish/v1/JsonSchemas/X") == 1
