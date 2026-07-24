"""Dual-mode-style coverage for DellRaidService.CheckVDValues."""
import json
from pathlib import Path

import pytest
from vendor_corpus import corpus_dir

from redfish_ctl.actions.action_policy import Destructiveness, classify
from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.raid.cmd_dell_raid_check_values import DellRaidCheckValues
from redfish_ctl.redfish_manager import CommandResult

DELL_CORPUS = corpus_dir(
    Path(__file__).parent.parent / "dell_xr8620t_corpus.tar.gz",
    "10.252.252.209",
)
DELL_INDEX = {path.name.lower(): path for path in DELL_CORPUS.glob("*.json")}
RAID_SERVICE = "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellRaidService"
CHECK_TARGET = f"{RAID_SERVICE}/Actions/DellRaidService.CheckVDValues"


def _fixture_for_path(path):
    """Return the extracted Dell fixture matching a Redfish path."""
    name = "_" + path.strip("/").replace("/", "_") + ".json"
    return DELL_INDEX.get(name.lower())


def _corpus_body(path):
    """Return one Dell corpus fixture body as JSON."""
    fixture = _fixture_for_path(path)
    if fixture is None:
        raise AssertionError(f"missing Dell fixture for {path}")
    return json.loads(fixture.read_text())


@pytest.fixture
def dell_raid_manager_factory():
    """Serve the committed Dell corpus over requests-mock."""
    requests_mock = pytest.importorskip("requests_mock")
    started = []

    def factory(service_body=None, post_body=None):
        requests = []

        def get_cb(request, context):
            requests.append(request)
            if request.path.lower() == RAID_SERVICE.lower() and service_body is not None:
                context.status_code = 200
                return json.dumps(service_body)
            fixture = _fixture_for_path(request.path)
            if fixture is None:
                context.status_code = 404
                return json.dumps({"error": f"no fixture for {request.path}"})
            context.status_code = 200
            return fixture.read_text()

        def post_cb(request, context):
            requests.append(request)
            context.status_code = 200
            return json.dumps(post_body or {"Status": "Valid"})

        mocker = requests_mock.Mocker()
        mocker.start()
        started.append(mocker)
        mocker.get(requests_mock.ANY, text=get_cb)
        mocker.post(requests_mock.ANY, text=post_cb)
        manager = IDracManager(
            idrac_ip="mock-dell-raid",
            idrac_username="root",
            idrac_password="mock",
            insecure=True,
            is_debug=False,
        )
        return manager, requests

    yield factory

    for mocker in reversed(started):
        mocker.stop()


def _post_requests(requests):
    """Return POST requests recorded by the mock Redfish transport."""
    return [request for request in requests if request.method == "POST"]


def test_dell_raid_check_values_lists_corpus_target_without_posting(
    dell_raid_manager_factory,
):
    """With no values, the command lists the advertised CheckVDValues target."""
    manager, requests = dell_raid_manager_factory()

    result = manager.sync_invoke(
        ApiRequestType.DellRaidCheckValues,
        "dell-raid-check-values",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == [{
        "Action": "check-vd-values",
        "FullType": "#DellRaidService.CheckVDValues",
        "Resource": RAID_SERVICE,
        "Target": CHECK_TARGET,
        "Description": "validate proposed Dell RAID virtual-disk values",
        "Parameters": {
            "VDPropNameArrayIn": [
                "RAIDLevel",
                "Size",
                "SpanDepth",
                "SpanLength",
                "StartingLBA",
                "T10PIStatus",
            ],
            "VDPropValueArrayIn": [],
        },
    }]
    assert _post_requests(requests) == []


def test_dell_raid_check_values_dry_run_validates_payload_without_posting(
    dell_raid_manager_factory,
):
    """--dry_run resolves the target and validates property names."""
    manager, requests = dell_raid_manager_factory()

    result = manager.sync_invoke(
        ApiRequestType.DellRaidCheckValues,
        "dell-raid-check-values",
        property_names=["RAIDLevel", "Size"],
        property_values=["RAID1", "1024"],
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == {
        "dry_run": True,
        "action": "#DellRaidService.CheckVDValues",
        "target": CHECK_TARGET,
        "payload": {
            "VDPropNameArrayIn": ["RAIDLevel", "Size"],
            "VDPropValueArrayIn": ["RAID1", "1024"],
        },
        "level": "read_only",
        "blocked": None,
    }
    assert _post_requests(requests) == []


def test_dell_raid_check_values_posts_read_only_action_by_default(
    dell_raid_manager_factory,
):
    """CheckVDValues is read-only, so the selected validation POSTs by default."""
    manager, requests = dell_raid_manager_factory(
        post_body={"Status": "Valid", "Checked": ["RAIDLevel"]}
    )

    result = manager.sync_invoke(
        ApiRequestType.DellRaidCheckValues,
        "dell-raid-check-values",
        property_names=["RAIDLevel"],
        property_values=["RAID1"],
    )

    posts = _post_requests(requests)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#DellRaidService.CheckVDValues"
    assert result.data["level"] == "read_only"
    assert result.data["target"] == CHECK_TARGET
    assert result.data["response"] == {"Status": "Valid", "Checked": ["RAIDLevel"]}
    assert len(posts) == 1
    assert posts[0].path.lower() == CHECK_TARGET.lower()
    assert posts[0].json() == {
        "VDPropNameArrayIn": ["RAIDLevel"],
        "VDPropValueArrayIn": ["RAID1"],
    }


def test_dell_raid_check_values_rejects_invalid_property_without_posting(
    dell_raid_manager_factory,
):
    """Advertised VDPropNameArrayIn allowable values are enforced."""
    manager, requests = dell_raid_manager_factory()

    result = manager.sync_invoke(
        ApiRequestType.DellRaidCheckValues,
        "dell-raid-check-values",
        property_names=["Bogus"],
        property_values=["x"],
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "invalid value for DellRaidService.CheckVDValues VDPropNameArrayIn: "
        "Bogus; allowed: RAIDLevel, Size, SpanDepth, SpanLength, StartingLBA, "
        "T10PIStatus"
    )
    assert result.data["validation_errors"][0]["parameter"] == "VDPropNameArrayIn"
    assert _post_requests(requests) == []


def test_dell_raid_check_values_rejects_mismatched_pairs(
    dell_raid_manager_factory,
):
    """Each virtual-disk property name must have a paired value."""
    manager, requests = dell_raid_manager_factory()

    with pytest.raises(InvalidArgument, match="must be supplied in pairs"):
        manager.sync_invoke(
            ApiRequestType.DellRaidCheckValues,
            "dell-raid-check-values",
            property_names=["RAIDLevel", "Size"],
            property_values=["RAID1"],
        )
    assert _post_requests(requests) == []


def test_dell_raid_check_values_missing_action_reports_available(
    dell_raid_manager_factory,
):
    """A DellRaidService without CheckVDValues reports the missing action."""
    service_body = _corpus_body(RAID_SERVICE)
    service_body["Actions"] = dict(service_body["Actions"])
    service_body["Actions"].pop("#DellRaidService.CheckVDValues")
    manager, requests = dell_raid_manager_factory(service_body=service_body)

    result = manager.sync_invoke(
        ApiRequestType.DellRaidCheckValues,
        "dell-raid-check-values",
        property_names=["RAIDLevel"],
        property_values=["RAID1"],
    )

    assert result.error == "Dell RAID CheckVDValues action not found"
    assert result.data["action"] == "#DellRaidService.CheckVDValues"
    assert "AssignSpare" in result.data["available"]
    assert _post_requests(requests) == []


def test_dell_raid_check_values_policy_and_registry():
    """CheckVDValues is classified read-only and the command is registered."""
    assert classify("#DellRaidService.CheckVDValues") is Destructiveness.READ_ONLY

    registry = IDracManager().get_registry()
    assert registry[ApiRequestType.DellRaidCheckValues][
        "dell-raid-check-values"
    ] is DellRaidCheckValues

    cmd_parser, cmd_name, cmd_help = DellRaidCheckValues.register_subcommand(
        DellRaidCheckValues
    )
    help_text = cmd_parser.format_help()

    assert cmd_name == "dell-raid-check-values"
    assert "Dell RAID" in cmd_help
    assert "--property" in help_text
    assert "--value" in help_text
