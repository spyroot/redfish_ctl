"""Dual-mode-style coverage for Dell MetricService action commands."""
import json
from pathlib import Path

import pytest
from vendor_corpus import corpus_dir

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.metrics.cmd_dell_metric_actions import DellMetricActions
from redfish_ctl.redfish_manager import CommandResult

DELL_CORPUS = corpus_dir(
    Path(__file__).parent.parent / "dell_xr8620t_corpus.tar.gz", "10.252.252.209"
)
DELL_INDEX = {path.name.lower(): path for path in DELL_CORPUS.glob("*.json")}
METRIC_SERVICE = "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellMetricService"
CONTROL_ACTION = "#DellMetricService.ControlMetrics"
CONTROL_TARGET = f"{METRIC_SERVICE}/Actions/DellMetricService.ControlMetrics"
EXPORT_ACTION = "#DellMetricService.ExportThermalHistory"
EXPORT_TARGET = f"{METRIC_SERVICE}/Actions/DellMetricService.ExportThermalHistory"


def _fixture_for_path(path):
    """Return the extracted Dell fixture matching a Redfish path.

    :param path: request path from requests-mock.
    :return: fixture path, or None when the corpus lacks the resource.
    """
    name = "_" + path.strip("/").replace("/", "_") + ".json"
    return DELL_INDEX.get(name.lower())


@pytest.fixture
def dell_metric_manager():
    """Serve the committed Dell corpus over requests-mock.

    :return: tuple of IDracManager and recorded requests list.
    """
    requests_mock = pytest.importorskip("requests_mock")
    requests = []

    def get_cb(request, context):
        requests.append(request)
        fixture = _fixture_for_path(request.path)
        if fixture is None:
            context.status_code = 404
            return json.dumps({"error": f"no fixture for {request.path}"})
        context.status_code = 200
        return fixture.read_text()

    def post_cb(request, context):
        requests.append(request)
        context.status_code = 202
        context.headers["Location"] = "/redfish/v1/TaskService/Tasks/dell-metric-1"
        return json.dumps({
            "Task": {"@odata.id": "/redfish/v1/TaskService/Tasks/dell-metric-1"}
        })

    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=get_cb)
        mocker.post(requests_mock.ANY, text=post_cb)
        manager = IDracManager(
            idrac_ip="mock-dell-metric",
            idrac_username="root",
            idrac_password="mock",
            insecure=True,
            is_debug=False,
        )
        yield manager, requests


def _post_requests(requests):
    """Return POST requests recorded by the mock Redfish transport.

    :param requests: recorded requests-mock request objects.
    :return: list of POST requests.
    """
    return [request for request in requests if request.method == "POST"]


def test_dell_metric_actions_lists_corpus_targets(dell_metric_manager):
    """Without an action, Dell MetricService targets are listed without POSTs."""
    manager, requests = dell_metric_manager

    result = manager.sync_invoke(
        ApiRequestType.DellMetricActions,
        "dell-metric-actions",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    rows = {row["Action"]: row for row in result.data}
    assert rows["control-metrics"]["Target"] == CONTROL_TARGET
    assert rows["control-metrics"]["AllowableValues"] == {
        "MetricCollectionEnabled": ["Reset"]
    }
    assert rows["export-thermal-history"]["Target"] == EXPORT_TARGET
    assert rows["export-thermal-history"]["AllowableValues"] == {
        "FileType": ["CSV", "XML"],
        "ShareType": ["CIFS", "NFS"],
    }
    assert _post_requests(requests) == []


def test_control_metrics_defaults_to_dry_run(dell_metric_manager):
    """ControlMetrics previews the Reset payload by default."""
    manager, requests = dell_metric_manager

    result = manager.sync_invoke(
        ApiRequestType.DellMetricActions,
        "dell-metric-actions",
        action="control-metrics",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == CONTROL_ACTION
    assert result.data["target"] == CONTROL_TARGET
    assert result.data["payload"] == {"MetricCollectionEnabled": "Reset"}
    assert result.data["level"] == "destructive"
    assert result.data["blocked"] == "destructive action requires --confirm"
    assert _post_requests(requests) == []


def test_control_metrics_confirm_posts_payload(dell_metric_manager):
    """ControlMetrics --confirm POSTs the advertised Reset payload."""
    manager, requests = dell_metric_manager

    result = manager.sync_invoke(
        ApiRequestType.DellMetricActions,
        "dell-metric-actions",
        action="control-metrics",
        confirm=True,
    )

    posts = _post_requests(requests)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == CONTROL_ACTION
    assert result.data["target"] == CONTROL_TARGET
    assert len(posts) == 1
    assert posts[0].path.lower() == CONTROL_TARGET.lower()
    assert posts[0].json() == {"MetricCollectionEnabled": "Reset"}


def test_export_thermal_history_preview_masks_share_password(
    dell_metric_manager,
    monkeypatch,
):
    """ExportThermalHistory dry-run masks the share password and avoids POST."""
    manager, requests = dell_metric_manager
    monkeypatch.setenv("THERMAL_SHARE_PASSWORD", "secret-value")

    result = manager.sync_invoke(
        ApiRequestType.DellMetricActions,
        "dell-metric-actions",
        action="export-thermal-history",
        share_address="192.0.2.25",
        share_name="/exports/thermal",
        file_name="thermal.csv",
        share_username="exporter",
        share_password_env="THERMAL_SHARE_PASSWORD",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == EXPORT_ACTION
    assert result.data["target"] == EXPORT_TARGET
    assert result.data["payload"] == {
        "FileType": "CSV",
        "ShareType": "NFS",
        "IPAddress": "192.0.2.25",
        "ShareName": "/exports/thermal",
        "FileName": "thermal.csv",
        "UserName": "exporter",
        "Password": "********",
    }
    assert _post_requests(requests) == []


def test_export_thermal_history_confirm_posts_unmasked_password(
    dell_metric_manager,
    monkeypatch,
):
    """ExportThermalHistory --confirm uses the real password only in the POST."""
    manager, requests = dell_metric_manager
    monkeypatch.setenv("THERMAL_SHARE_PASSWORD", "secret-value")

    result = manager.sync_invoke(
        ApiRequestType.DellMetricActions,
        "dell-metric-actions",
        action="export-thermal-history",
        share_address="192.0.2.25",
        share_name="/exports/thermal",
        share_password_env="THERMAL_SHARE_PASSWORD",
        confirm=True,
    )

    posts = _post_requests(requests)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == EXPORT_ACTION
    assert result.data["target"] == EXPORT_TARGET
    assert len(posts) == 1
    assert posts[0].path.lower() == EXPORT_TARGET.lower()
    assert posts[0].json()["Password"] == "secret-value"


def test_export_thermal_history_requires_share_target(dell_metric_manager):
    """ExportThermalHistory rejects missing share fields before any POST."""
    manager, requests = dell_metric_manager

    with pytest.raises(InvalidArgument, match="IPAddress, ShareName"):
        manager.sync_invoke(
            ApiRequestType.DellMetricActions,
            "dell-metric-actions",
            action="export-thermal-history",
            confirm=True,
        )

    assert _post_requests(requests) == []


def test_export_thermal_history_validates_inline_allowable_values(
    dell_metric_manager,
):
    """ExportThermalHistory rejects FileType values outside the action metadata."""
    manager, requests = dell_metric_manager

    result = manager.sync_invoke(
        ApiRequestType.DellMetricActions,
        "dell-metric-actions",
        action="export-thermal-history",
        file_type="TXT",
        share_address="192.0.2.25",
        share_name="/exports/thermal",
    )

    assert isinstance(result, CommandResult)
    assert result.data["validation_errors"][0] == {
        "parameter": "FileType",
        "value": "TXT",
        "allowed": ["CSV", "XML"],
    }
    assert result.error == (
        "invalid value for DellMetricService.ExportThermalHistory FileType: "
        "TXT; allowed: CSV, XML"
    )
    assert _post_requests(requests) == []


def test_dell_metric_actions_is_registered():
    """The dell-metric-actions command is wired into the package registry."""
    registry = IDracManager._registry
    assert registry[ApiRequestType.DellMetricActions]["dell-metric-actions"] is (
        DellMetricActions
    )

    cmd_parser, cmd_name, cmd_help = DellMetricActions.register_subcommand(
        DellMetricActions
    )
    help_text = cmd_parser.format_help()

    assert cmd_name == "dell-metric-actions"
    assert "MetricService" in cmd_help
    assert "--action" in help_text
    assert "--share-password-env" in help_text
