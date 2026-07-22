"""Dual-mode-style tests for guarded NetworkAdapter.Reset actions."""
import copy

import pytest

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.network.cmd_network_adapter_reset import NetworkAdapterReset
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from test_roundtrip_budget import projected_walltime

ADAPTER = "IO_Board_0_CX8_0"
ADAPTER_URI = f"/redfish/v1/Chassis/IO_Board_0/NetworkAdapters/{ADAPTER}"
RESET_TARGET = f"{ADAPTER_URI}/Actions/NetworkAdapter.Reset"


@pytest.fixture
def gb300_corpus_mock(redfish_mock_factory):
    """Return a manager and mock service backed by the GB300 corpus.

    :return: tuple of Redfish manager and mock service.
    """
    yield redfish_mock_factory("supermicro")


def _post_requests(service):
    """Return POST requests recorded by the mock Redfish service.

    :param service: mock Redfish service.
    :return: recorded POST requests.
    """
    return [request for request in service.requests if request.method == "POST"]


def _get_requests(service):
    """Return GET requests recorded by the mock Redfish service.

    :param service: mock Redfish service.
    :return: recorded GET requests.
    """
    return [request for request in service.requests if request.method == "GET"]


def _replace_adapter(service, adapter):
    """Replace the GB300 adapter fixture body in the mock overlay.

    :param service: mock Redfish service.
    :param adapter: replacement adapter body.
    """
    service._overlay[ADAPTER_URI] = adapter
    service._overlay[ADAPTER_URI.lower()] = adapter


def test_network_adapter_reset_lists_gb300_targets_without_post(gb300_corpus_mock):
    """Listing reports the GB300 reset-capable adapters without mutating."""
    manager, service = gb300_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.NetworkAdapterReset,
        "network-adapter-reset",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    rows = result.data["resettable_adapters"]
    row = next(item for item in rows if item["Adapter"] == ADAPTER)
    assert row["Resource"] == ADAPTER_URI
    assert row["Target"] == RESET_TARGET
    assert row["ResetTypes"] == ["ForceRestart"]
    assert _post_requests(service) == []


def test_network_adapter_reset_reports_root_read_failures(gb300_corpus_mock):
    """A failed top-level Chassis read is reported instead of an empty list."""
    requests_mock = pytest.importorskip("requests_mock")
    manager, service = gb300_corpus_mock

    def get_cb(request, context):
        if request.path.rstrip("/").lower() == "/redfish/v1/chassis":
            service.requests.append(request)
            context.status_code = 401
            return '{"error": "denied"}'
        return service.get_cb(request, context)

    service.mocker.get(requests_mock.ANY, text=get_cb)

    result = manager.sync_invoke(
        ApiRequestType.NetworkAdapterReset,
        "network-adapter-reset",
    )

    assert isinstance(result, CommandResult)
    assert result.data is None
    assert "failed to read /redfish/v1/Chassis" in result.error
    # 401 is normalized to the Redfish error envelope (HTTP status preserved),
    # not the old hardcoded "Authentication failed." string.
    assert "HTTP 401" in result.error
    assert _post_requests(service) == []


def test_network_adapter_reset_defaults_to_dry_run(gb300_corpus_mock):
    """A selected NetworkAdapter.Reset previews by default and sends no POST."""
    manager, service = gb300_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.NetworkAdapterReset,
        "network-adapter-reset",
        adapter=ADAPTER,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == "#NetworkAdapter.Reset"
    assert result.data["adapter"] == ADAPTER
    assert result.data["resource"] == ADAPTER_URI
    assert result.data["target"] == RESET_TARGET
    assert result.data["payload"] == {"ResetType": "ForceRestart"}
    assert result.data["level"] == "destructive"
    assert result.data["blocked"] == "destructive action requires --confirm"
    assert _post_requests(service) == []


def test_network_adapter_reset_exact_uri_skips_full_chassis_crawl(gb300_corpus_mock):
    """A full Redfish URI selector fetches only the named adapter before preview."""
    manager, service = gb300_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.NetworkAdapterReset,
        "network-adapter-reset",
        adapter=ADAPTER_URI,
    )

    get_paths = [request.path.lower() for request in _get_requests(service)]
    assert result.error is None
    assert result.data["payload"] == {"ResetType": "ForceRestart"}
    assert get_paths.count(ADAPTER_URI.lower()) == 2
    assert "/redfish/v1/chassis" not in get_paths
    assert projected_walltime(service, "india-vpn-to-us") <= 0.61
    assert _post_requests(service) == []


def test_network_adapter_reset_confirm_posts_discovered_action(gb300_corpus_mock):
    """--confirm POSTs NetworkAdapter.Reset to exactly one discovered target."""
    manager, service = gb300_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.NetworkAdapterReset,
        "network-adapter-reset",
        adapter=ADAPTER_URI,
        reset_type="ForceRestart",
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#NetworkAdapter.Reset"
    assert result.data["adapter"] == ADAPTER
    assert result.data["level"] == "destructive"
    assert len(posts) == 1
    assert posts[0].path.lower() == RESET_TARGET.lower()
    assert posts[0].json() == {"ResetType": "ForceRestart"}


def test_network_adapter_reset_requires_type_when_multiple_values(gb300_corpus_mock):
    """Ambiguous advertised ResetType choices require an explicit selector."""
    manager, service = gb300_corpus_mock
    adapter = copy.deepcopy(service._state(ADAPTER_URI.lower()))
    adapter["Actions"]["#NetworkAdapter.Reset"][
        "ResetType@Redfish.AllowableValues"
    ] = ["ForceRestart", "GracefulRestart"]
    _replace_adapter(service, adapter)

    with pytest.raises(InvalidArgument, match="pass --reset-type explicitly"):
        manager.sync_invoke(
            ApiRequestType.NetworkAdapterReset,
            "network-adapter-reset",
            adapter=ADAPTER_URI,
            confirm=True,
        )

    assert _post_requests(service) == []


def test_network_adapter_reset_rejects_invalid_reset_type(gb300_corpus_mock):
    """Invalid ResetType values are rejected before any POST."""
    manager, service = gb300_corpus_mock

    with pytest.raises(InvalidArgument, match="invalid ResetType"):
        manager.sync_invoke(
            ApiRequestType.NetworkAdapterReset,
            "network-adapter-reset",
            adapter=ADAPTER,
            reset_type="GracefulRestart",
            confirm=True,
        )

    assert _post_requests(service) == []


def test_network_adapter_reset_validates_type_without_allowables(gb300_corpus_mock):
    """A missing AllowableValues list still rejects unknown ResetType strings."""
    manager, service = gb300_corpus_mock
    adapter = copy.deepcopy(service._state(ADAPTER_URI.lower()))
    adapter["Actions"]["#NetworkAdapter.Reset"].pop(
        "ResetType@Redfish.AllowableValues"
    )
    _replace_adapter(service, adapter)

    with pytest.raises(InvalidArgument, match="invalid ResetType"):
        manager.sync_invoke(
            ApiRequestType.NetworkAdapterReset,
            "network-adapter-reset",
            adapter=ADAPTER_URI,
            reset_type="VendorMagic",
            confirm=True,
        )

    assert _post_requests(service) == []


def test_network_adapter_reset_exposes_cli_entrypoint():
    """The network-adapter-reset command is wired into the package registry."""
    registry = IDracManager().get_registry()
    assert registry[ApiRequestType.NetworkAdapterReset]["network-adapter-reset"] is (
        NetworkAdapterReset
    )

    cmd_parser, cmd_name, cmd_help = NetworkAdapterReset.register_subcommand(
        NetworkAdapterReset
    )

    help_text = cmd_parser.format_help()
    assert "--adapter" in help_text
    assert "--reset-type" in help_text
    assert cmd_name == "network-adapter-reset"
    assert "NetworkAdapter.Reset" in cmd_help
