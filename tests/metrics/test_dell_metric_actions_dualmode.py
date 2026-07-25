"""Dual-mode-style coverage for Dell MetricService action commands."""
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.metrics.cmd_dell_metric_actions import DellMetricActions
from redfish_ctl.redfish_manager import CommandResult

DELL_CORPUS = corpus_dir(
    Path(__file__).parent.parent / "dell_xr8620t_corpus.tar.gz", "10.252.252.209"
)
METRIC_SERVICE = "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellMetricService"
CONTROL_ACTION = "#DellMetricService.ControlMetrics"
CONTROL_TARGET = f"{METRIC_SERVICE}/Actions/DellMetricService.ControlMetrics"
EXPORT_ACTION = "#DellMetricService.ExportThermalHistory"
EXPORT_TARGET = f"{METRIC_SERVICE}/Actions/DellMetricService.ExportThermalHistory"


@pytest.fixture
def dell_metric_mock():
    """Return a manager and mock service backed by the Dell XR8620t corpus.

    The vendor-faithful service realizes an Action POST the Dell way: 202 plus
    a ``JID_`` OEM job id in the Location header, never a DMTF-generic token.

    :return: tuple of IDracManager and the recording MockRedfishService.
    """
    requests_mock = pytest.importorskip("requests_mock")
    service = MockRedfishService(
        DELL_CORPUS,
        index=_build_fixture_index(DELL_CORPUS),
    )
    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=service.get_cb)
        mocker.patch(requests_mock.ANY, text=service.patch_cb)
        mocker.post(requests_mock.ANY, text=service.post_cb)
        mocker.delete(requests_mock.ANY, text=service.delete_cb)
        service.mocker = mocker
        yield (
            IDracManager(
                idrac_ip="mock-dell-metric",
                idrac_username="root",
                idrac_password="mock",
                insecure=True,
                is_debug=False,
            ),
            service,
        )


def _post_requests(service):
    """Return POST requests recorded by the mock Redfish service.

    :param service: the recording MockRedfishService.
    :return: list of POST requests.
    """
    return [request for request in service.requests if request.method == "POST"]


def test_dell_metric_actions_lists_corpus_targets(dell_metric_mock):
    """Without an action, Dell MetricService targets are listed without POSTs."""
    manager, service = dell_metric_mock

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
    assert _post_requests(service) == []


def test_control_metrics_defaults_to_dry_run(dell_metric_mock):
    """ControlMetrics previews the Reset payload by default."""
    manager, service = dell_metric_mock

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
    assert _post_requests(service) == []


def test_control_metrics_confirm_posts_payload(dell_metric_mock):
    """ControlMetrics --confirm POSTs; the Dell lens realizes a ``JID_`` job id."""
    manager, service = dell_metric_mock

    result = manager.sync_invoke(
        ApiRequestType.DellMetricActions,
        "dell-metric-actions",
        action="control-metrics",
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == CONTROL_ACTION
    assert result.data["target"] == CONTROL_TARGET
    assert result.data["task_id"] == service.JOB_ID
    assert service.JOB_ID.startswith("JID_")
    assert len(posts) == 1
    assert posts[0].path.lower() == CONTROL_TARGET.lower()
    assert posts[0].json() == {"MetricCollectionEnabled": "Reset"}


def test_export_thermal_history_preview_masks_share_password(
    dell_metric_mock,
    monkeypatch,
):
    """ExportThermalHistory dry-run masks the share password and avoids POST."""
    manager, service = dell_metric_mock
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
    assert _post_requests(service) == []


def test_export_thermal_history_confirm_posts_unmasked_password(
    dell_metric_mock,
    monkeypatch,
):
    """ExportThermalHistory --confirm uses the real password only in the POST."""
    manager, service = dell_metric_mock
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

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == EXPORT_ACTION
    assert result.data["target"] == EXPORT_TARGET
    assert len(posts) == 1
    assert posts[0].path.lower() == EXPORT_TARGET.lower()
    assert posts[0].json()["Password"] == "secret-value"


def test_export_thermal_history_requires_share_target(dell_metric_mock):
    """ExportThermalHistory rejects missing share fields before any POST."""
    manager, service = dell_metric_mock

    with pytest.raises(InvalidArgument, match="IPAddress, ShareName"):
        manager.sync_invoke(
            ApiRequestType.DellMetricActions,
            "dell-metric-actions",
            action="export-thermal-history",
            confirm=True,
        )

    assert _post_requests(service) == []


def test_export_thermal_history_validates_inline_allowable_values(
    dell_metric_mock,
):
    """ExportThermalHistory rejects FileType values outside the action metadata."""
    manager, service = dell_metric_mock

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
    assert _post_requests(service) == []


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
