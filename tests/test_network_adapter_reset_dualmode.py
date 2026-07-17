"""Dual-mode-style tests for guarded NetworkAdapter.Reset actions."""
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.network.cmd_network_adapter_reset import NetworkAdapterReset
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_manager_shared import ApiRequestType

REPO_ROOT = Path(__file__).resolve().parents[1]
GB300_CORPUS = corpus_dir(
    REPO_ROOT / "tests" / "supermicro_gb300_corpus.tar.gz",
    "172.25.230.37",
)
ADAPTER = "IO_Board_0_CX8_0"
ADAPTER_URI = f"/redfish/v1/Chassis/IO_Board_0/NetworkAdapters/{ADAPTER}"
RESET_TARGET = f"{ADAPTER_URI}/Actions/NetworkAdapter.Reset"


@pytest.fixture
def gb300_corpus_mock():
    """Return a manager and mock service backed by the GB300 corpus.

    :return: tuple of Redfish manager and mock service.
    """
    requests_mock = pytest.importorskip("requests_mock")
    service = MockRedfishService(
        GB300_CORPUS,
        index=_build_fixture_index(GB300_CORPUS),
    )
    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=service.get_cb)
        mocker.patch(requests_mock.ANY, text=service.patch_cb)
        mocker.post(requests_mock.ANY, text=service.post_cb)
        mocker.delete(requests_mock.ANY, text=service.delete_cb)
        service.mocker = mocker
        yield (
            RedfishManagerBase(
                idrac_ip="mock-gb300",
                idrac_username="root",
                idrac_password="mock",
                insecure=True,
                is_debug=False,
            ),
            service,
        )


def _post_requests(service):
    """Return POST requests recorded by the mock Redfish service.

    :param service: mock Redfish service.
    :return: recorded POST requests.
    """
    return [request for request in service.requests if request.method == "POST"]


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
    assert len(rows) == 5
    row = next(item for item in rows if item["Adapter"] == ADAPTER)
    assert row["Resource"] == ADAPTER_URI
    assert row["Target"] == RESET_TARGET
    assert row["ResetTypes"] == ["ForceRestart"]
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


def test_network_adapter_reset_exposes_cli_entrypoint():
    """The network-adapter-reset command is wired into the package registry."""
    registry = RedfishManagerBase().get_registry()
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
