"""Native DSP2043 mockup mode of the sandbox mock BMC server.

A DSP2043 mockup profile stores each Redfish resource as ``<path>/index.json``
and the directory tree IS the API structure, 1:1; the profile root's
``index.json`` is the service root. These tests pin that the ``--mockup-dir``
mode of ``k8s/sandbox/mock_bmc_server.py`` serves that tree verbatim — no
synthesis, no rewriting — plus the DSP0266 1.24.0 section 6.7 spec-defined
URIs (``/redfish``, ``$metadata``, ``odata``, trailing-slash equivalence).
Bundle-backed tests skip cleanly when the LFS zip is a bare pointer.

Author Mus spyroot@gmail.com
"""
from __future__ import annotations

import importlib.util
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from dmtf_mockup import is_lfs_pointer, mockup_profile_dir

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_MODULE = REPO_ROOT / "k8s" / "sandbox" / "mock_bmc_server.py"
BUNDLE = (
    REPO_ROOT / "spec" / "dmtf" / "redfish" / "2026.1" / "mockups"
    / "DSP2043_2026.1.zip"
)
METRIC_DEFINITIONS = "/redfish/v1/TelemetryService/MetricDefinitions"

requires_bundle = pytest.mark.skipif(
    not BUNDLE.exists() or is_lfs_pointer(BUNDLE),
    reason="DSP2043_2026.1.zip is absent or a bare Git-LFS pointer "
    "(fetch with: git lfs pull)",
)


def _load_server_module():
    """Load the mock BMC server module from its file path.

    :return: the imported ``mock_bmc_server`` module object.
    """
    spec = importlib.util.spec_from_file_location("mock_bmc_server", SERVER_MODULE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _get_raw(base: str, path: str) -> tuple[int, bytes, str]:
    """GET a path from the mock, returning status, raw body, content type.

    :param base: the server's ``http://host:port`` base URL.
    :param path: request path to fetch.
    :return: ``(status, body bytes, Content-Type header)``.
    """
    try:
        with urllib.request.urlopen(base + path, timeout=5) as response:
            return (
                response.status,
                response.read(),
                response.headers.get("Content-Type", ""),
            )
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers.get("Content-Type", "")


def _subdirs(tree: Path) -> set[str]:
    """Names of the immediate subdirectories of a mockup collection dir.

    :param tree: the collection's directory in the extracted profile.
    :return: the set of member directory names.
    """
    return {entry.name for entry in tree.iterdir() if entry.is_dir()}


@pytest.fixture(scope="module")
def profile() -> Path:
    """The extracted ``public-telemetry`` profile root (cached per process)."""
    return mockup_profile_dir(BUNDLE, "public-telemetry")


def _write_min_profile(root: Path, prefixed: bool) -> Path:
    """Write a minimal synthetic DSP2043 profile tree.

    :param root: directory to create the profile under.
    :param prefixed: when True nest the tree under a ``redfish/v1/`` prefix
        and ship a ``redfish/index.json`` version object.
    :return: the profile root directory.
    """
    service = root / "redfish" / "v1" if prefixed else root
    service.mkdir(parents=True, exist_ok=True)
    (service / "index.json").write_text(
        json.dumps({"@odata.id": "/redfish/v1/"}), encoding="utf-8"
    )
    if prefixed:
        (root / "redfish" / "index.json").write_text(
            json.dumps({"v1": "/redfish/v1/"}), encoding="utf-8"
        )
    return root


# --- layout probing and resolution (synthetic trees, no bundle needed) ---------


def test_mockup_service_root_probes_prefixed_and_root_direct_layouts(
    tmp_path: Path,
) -> None:
    """Both DSP2043 layouts resolve; an empty dir fails closed.

    The 2026.1 bundle profiles start directly at the service root, but other
    mockup trees nest under ``redfish/v1/`` — the probe must support both so
    the mode is not bound to one bundle revision.
    """
    module = _load_server_module()
    direct = _write_min_profile(tmp_path / "direct", prefixed=False)
    prefixed = _write_min_profile(tmp_path / "prefixed", prefixed=True)

    assert module.mockup_service_root(direct) == direct
    assert module.mockup_service_root(prefixed) == prefixed / "redfish" / "v1"

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError, match="no DSP2043 service root"):
        module.mockup_service_root(empty)


def test_mockup_prefixed_layout_serves_version_object_from_file(
    tmp_path: Path,
) -> None:
    """A ``redfish/v1``-prefixed profile serves its own ``redfish/index.json``.

    DSP0266 1.24.0 section 6.7 Table 4 defines ``/redfish``; when the tree
    ships the document it must be served from the file, not synthesized.
    """
    module = _load_server_module()
    prefixed = _write_min_profile(tmp_path / "prefixed", prefixed=True)

    with module.run_server("127.0.0.1", 0, mockup_dir=prefixed) as server:
        base = "http://{}:{}".format(*server.server_address)
        status, body, _ = _get_raw(base, "/redfish")
        root_status, root_body, _ = _get_raw(base, "/redfish/v1")

    assert status == 200
    assert body == (prefixed / "redfish" / "index.json").read_bytes()
    assert root_status == 200
    assert json.loads(root_body) == {"@odata.id": "/redfish/v1/"}


def test_mockup_resolution_rejects_traversal_and_foreign_paths(
    tmp_path: Path,
) -> None:
    """Paths outside ``/redfish/v1`` and ``..`` traversal resolve to nothing.

    The request path is untrusted input: a decoded ``..`` segment must never
    escape the profile tree, and non-Redfish paths must not map to files.
    """
    module = _load_server_module()
    direct = _write_min_profile(tmp_path / "direct", prefixed=False)
    secret = tmp_path / "secret.json"
    secret.write_text('{"leaked": true}', encoding="utf-8")

    resolve = module.mockup_file_for_redfish_path
    assert resolve(direct, "/redfish/v1/../secret.json") is None
    assert resolve(direct, "/redfish/v1/%2e%2e/secret.json") is None
    assert resolve(direct, "/etc/passwd") is None
    assert resolve(direct, "/redfish/v1") == direct / "index.json"


def test_mockup_mode_refuses_write_engines(tmp_path: Path) -> None:
    """Combining ``--mockup-dir`` with a write engine fails closed.

    The mockup tree is a static spec artifact; accepting a replay trace or
    mutation rules would silently drop the writes instead of replaying them.
    """
    module = _load_server_module()
    direct = _write_min_profile(tmp_path / "direct", prefixed=False)

    with pytest.raises(ValueError, match="GET-only"):
        with module.run_server(
            "127.0.0.1", 0, mockup_dir=direct, replay_trace=tmp_path / "t.yaml"
        ):
            pass  # pragma: no cover - the context manager raises on entry.


def test_mockup_non_get_methods_return_405(tmp_path: Path) -> None:
    """Every non-GET method is refused with 405 and ``Allow: GET``.

    The sim contract (``specs/sim/dmtf-sim-contract.yaml``) provides GET as
    the entire method surface: writes because an accepted write would be
    invented behavior, HEAD/OPTIONS because they are outside the declared
    surface (a HEAD body is suppressed per HTTP).
    """
    module = _load_server_module()
    direct = _write_min_profile(tmp_path / "direct", prefixed=False)

    with module.run_server("127.0.0.1", 0, mockup_dir=direct) as server:
        base = "http://{}:{}".format(*server.server_address)
        for method in ("POST", "PATCH", "PUT", "DELETE", "HEAD", "OPTIONS"):
            body = b"{}" if method in ("POST", "PATCH", "PUT") else None
            request = urllib.request.Request(
                base + "/redfish/v1", data=body, method=method
            )
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(request, timeout=5)
            assert exc_info.value.code == 405, method
            assert exc_info.value.headers["Allow"] == "GET", method
            if method == "HEAD":
                assert exc_info.value.read() == b""


# --- the DMTF public-telemetry profile (bundle-backed) --------------------------


@requires_bundle
def test_mockup_serves_service_root_for_both_slash_spellings(
    profile: Path,
) -> None:
    """``/redfish/v1`` and ``/redfish/v1/`` both serve the root ``index.json``.

    DSP0266 1.24.0 section 6.7 Table 5 requires URIs to be processed with and
    without the trailing slash; the profile root's ``index.json`` IS the
    service root in the DSP2043 layout.
    """
    module = _load_server_module()
    on_disk = json.loads((profile / "index.json").read_bytes())

    with module.run_server("127.0.0.1", 0, mockup_dir=profile) as server:
        base = "http://{}:{}".format(*server.server_address)
        bare_status, bare_body, _ = _get_raw(base, "/redfish/v1")
        slash_status, slash_body, _ = _get_raw(base, "/redfish/v1/")

    assert bare_status == 200 and slash_status == 200
    assert bare_body == slash_body
    assert json.loads(bare_body) == on_disk


@requires_bundle
def test_mockup_collection_members_mirror_the_directory_tree(
    profile: Path,
) -> None:
    """A collection's Members are exactly its subdirectories — the 1:1 rule.

    The DSP2043 directory structure IS the API structure, so the Members the
    server returns must match the member directories on disk (enumerated from
    the tree, not hardcoded); ``PowerConsumedWatts`` is the operator-named
    example member.
    """
    module = _load_server_module()
    tree = profile / "TelemetryService" / "MetricDefinitions"

    with module.run_server("127.0.0.1", 0, mockup_dir=profile) as server:
        base = "http://{}:{}".format(*server.server_address)
        status, body, _ = _get_raw(base, METRIC_DEFINITIONS)

    assert status == 200
    payload = json.loads(body)
    assert payload == json.loads((tree / "index.json").read_bytes())
    member_ids = {
        member["@odata.id"].rstrip("/").rsplit("/", 1)[-1]
        for member in payload["Members"]
    }
    assert member_ids == _subdirs(tree)
    assert "PowerConsumedWatts" in member_ids


@requires_bundle
def test_mockup_leaf_resource_is_byte_faithful(profile: Path) -> None:
    """A leaf resource is returned as the exact bytes of its ``index.json``.

    Byte-faithfulness is the mode's contract: the mock reads the DMTF file
    and returns it — no synthesis, no rewriting, no re-serialization.
    """
    module = _load_server_module()
    leaf = METRIC_DEFINITIONS + "/PowerConsumedWatts"
    on_disk = (
        profile / "TelemetryService" / "MetricDefinitions"
        / "PowerConsumedWatts" / "index.json"
    ).read_bytes()

    with module.run_server("127.0.0.1", 0, mockup_dir=profile) as server:
        base = "http://{}:{}".format(*server.server_address)
        status, body, content_type = _get_raw(base, leaf)

    assert status == 200
    assert body == on_disk
    assert content_type == "application/json"


@requires_bundle
def test_mockup_missing_resource_returns_dmtf_error_404(profile: Path) -> None:
    """A path with no ``index.json`` in the tree is a 404 with a DMTF error body.

    Absence in the tree is the only 404 condition in this mode, and the body
    must be Redfish-shaped (an ``error`` object) so clients parse it.
    """
    module = _load_server_module()

    with module.run_server("127.0.0.1", 0, mockup_dir=profile) as server:
        base = "http://{}:{}".format(*server.server_address)
        status, body, content_type = _get_raw(base, "/redfish/v1/NoSuchResource")

    assert status == 404
    assert content_type == "application/json"
    payload = json.loads(body)
    assert payload["error"]["code"].startswith("Base.")
    assert "/redfish/v1/NoSuchResource" in payload["error"]["message"]


@requires_bundle
def test_mockup_redfish_version_object_is_spec_defined(profile: Path) -> None:
    """``/redfish`` serves the version object even on a root-direct profile.

    DSP0266 1.24.0 section 6.7 Table 4 requires ``/redfish``; the 2026.1
    profiles are root-direct (no ``redfish/index.json``), so the spec-defined
    ``{"v1": "/redfish/v1/"}`` document is synthesized — the one payload in
    this mode that is not a file, because the protocol defines it.
    """
    module = _load_server_module()
    assert not (profile / "redfish" / "index.json").exists()

    with module.run_server("127.0.0.1", 0, mockup_dir=profile) as server:
        base = "http://{}:{}".format(*server.server_address)
        bare_status, bare_body, content_type = _get_raw(base, "/redfish")
        slash_status, slash_body, _ = _get_raw(base, "/redfish/")

    assert bare_status == 200 and slash_status == 200
    assert content_type == "application/json"
    assert json.loads(bare_body) == {"v1": "/redfish/v1/"}
    assert slash_body == bare_body


@requires_bundle
def test_mockup_metadata_document_is_served_as_xml(profile: Path) -> None:
    """``/redfish/v1/$metadata`` is the XML metadata document, not JSON.

    DSP0266 1.24.0 section 6.7 Table 4 defines ``$metadata`` as the OData
    CSDL document; DSP2043 stores it as ``$metadata/index.xml`` and the mock
    must serve it byte-faithful with an XML content type, never JSON-wrapped.
    """
    module = _load_server_module()
    on_disk = (profile / "$metadata" / "index.xml").read_bytes()

    with module.run_server("127.0.0.1", 0, mockup_dir=profile) as server:
        base = "http://{}:{}".format(*server.server_address)
        status, body, content_type = _get_raw(base, "/redfish/v1/$metadata")

    assert status == 200
    assert content_type == "application/xml"
    assert body == on_disk


@requires_bundle
def test_mockup_odata_service_document_is_served(profile: Path) -> None:
    """``/redfish/v1/odata`` serves the profile's OData service document.

    DSP0266 1.24.0 section 6.7 Table 4 lists the OData service document;
    ``public-telemetry`` ships it as ``odata/index.json``, so the generic
    1:1 rule must resolve it with no special casing.
    """
    module = _load_server_module()
    on_disk = (profile / "odata" / "index.json").read_bytes()

    with module.run_server("127.0.0.1", 0, mockup_dir=profile) as server:
        base = "http://{}:{}".format(*server.server_address)
        status, body, content_type = _get_raw(base, "/redfish/v1/odata")

    assert status == 200
    assert content_type == "application/json"
    assert body == on_disk
    assert any(
        entry.get("url") == "/redfish/v1/" for entry in json.loads(body)["value"]
    )


@requires_bundle
def test_metric_definitions_command_reads_the_dmtf_mockup(profile: Path) -> None:
    """The real ``metric-definitions`` command walks the served mockup tree.

    End-to-end through the real command path (DMTF-generic lens: this tree is
    that lens's authoritative source): the command must return one row per
    MetricReportDefinition member enumerated from the tree itself, with each
    row's metric count matching the on-disk ``MetricProperties``.
    """
    from redfish_ctl.idrac_manager import IDracManager
    from redfish_ctl.redfish_api_common import ApiRequestType

    module = _load_server_module()
    tree = profile / "TelemetryService" / "MetricReportDefinitions"
    expected_counts = {
        name: len(
            json.loads((tree / name / "index.json").read_bytes()).get(
                "MetricProperties"
            )
            or []
        )
        for name in _subdirs(tree)
    }

    with module.run_server("127.0.0.1", 0, mockup_dir=profile) as server:
        host, port = server.server_address
        manager = IDracManager(
            host=host,
            port=port,
            username="root",
            password="mock",
            insecure=True,
            is_http=True,
        )
        result = manager.sync_invoke(
            ApiRequestType.SupermicroMetricReportDefinitions, "metric-definitions"
        )

    rows = {row["Definition"]: row for row in result.data}
    assert set(rows) == set(expected_counts)
    for name, row in rows.items():
        assert row["MetricCount"] == expected_counts[name]
