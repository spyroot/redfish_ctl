"""Offline BIOS staging and registry boundaries for iLO and Dell."""
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest

from redfish_ctl.cmd_exceptions import UnsupportedAction
from redfish_ctl.jobs.cmd_job_apply import JobApply
from redfish_ctl.redfish_api_common import ApiRequestType
from redfish_ctl.redfish_exceptions import RedfishForbidden
from redfish_ctl.redfish_manager import CommandResult

_ILO_BIOS_URI = "/redfish/v1/Systems/1/Bios"
_ILO_SETTINGS_URI = "/redfish/v1/systems/1/bios/settings/"
_DELL_SETTINGS_URI = "/redfish/v1/Systems/System.Embedded.1/Bios/Settings"


@pytest.fixture
def bios_spec(tmp_path: Path) -> str:
    """Return a self-contained spec for tests of SettingsObject discovery."""
    spec = tmp_path / "bios.json"
    spec.write_text('{"Attributes": {"DynamicPowerCapping": "Enabled"}}')
    return str(spec)


def test_bios_change_builds_payload_on_ilo(redfish_mock_factory) -> None:
    """Show mode resolves the standard registry without running a Dell commit."""
    mgr, svc = redfish_mock_factory("hpe")
    result = mgr.sync_invoke(ApiRequestType.BiosChangeSettings, "bios_change_settings",
                             attr_name="DynamicPowerCapping", attr_value="Enabled",
                             do_show=True, do_commit=True)
    attrs = result.data.get("Attributes", {})
    assert attrs.get("DynamicPowerCapping") == "Enabled"
    # applied as a pending change on reset (standard Redfish, no Dell Jobs queue)
    assert result.data.get("@Redfish.SettingsApplyTime", {}).get("ApplyTime") == "OnReset"
    assert all(r.method == "GET" for r in svc.mocker.request_history)
    assert all(not r.path.lower().endswith("/bios/biosregistry")
               for r in svc.mocker.request_history)


def test_bios_change_patches_settings_on_ilo(redfish_mock_factory) -> None:
    """The default (no --commit) path PATCHes the resolved Settings object on iLO."""
    mgr, svc = redfish_mock_factory("hpe")
    result = mgr.sync_invoke(ApiRequestType.BiosChangeSettings, "bios_change_settings",
                             attr_name="DynamicPowerCapping", attr_value="Enabled")
    assert result.error is None
    writes = [r for r in svc.mocker.request_history if r.method != "GET"]
    assert [(r.method, r.path) for r in writes] == [("PATCH", _ILO_SETTINGS_URI)]
    assert writes[0].json() == {
        "Attributes": {"DynamicPowerCapping": "Enabled"},
        "@Redfish.SettingsApplyTime": {"ApplyTime": "OnReset"},
    }


@pytest.mark.parametrize("route", ["single-system", "multiple-systems", "manager-link"])
def test_bios_change_stages_with_absolute_system_link(
    redfish_mock_factory, bios_spec: str, route: str,
) -> None:
    """Same-origin ComputerSystem IDs resolve to one usable local URI."""
    mgr, svc = redfish_mock_factory("hpe")
    system = {"@odata.id": f"https://{mgr.redfish_ip}/redfish/v1/Systems/1"}
    members = [system]
    if route == "multiple-systems":
        members.append({"@odata.id": "/redfish/v1/Systems/Other"})
        svc.mocker.get("/redfish/v1/Systems/Other", json={"Id": "Other"})
    elif route == "manager-link":
        members.clear()
        svc.mocker.get("/redfish/v1/Managers/1", json={"Links": {"ManagerForServers": [system]}})
    svc.mocker.get("/redfish/v1/Systems", json={"Members": members})

    result = mgr.sync_invoke(
        ApiRequestType.BiosChangeSettings, "bios_change_settings", from_spec=bios_spec,
    )

    assert result.error is None
    assert [(r.method, r.path) for r in svc.mocker.request_history if r.method != "GET"] == [
        ("PATCH", _ILO_SETTINGS_URI),
    ]


@pytest.mark.parametrize("option", ["do_commit", "commit_pending"])
def test_bios_change_rejects_dell_commit_before_staging_on_ilo(
    redfish_mock_factory, bios_spec: str, option: str,
) -> None:
    """A Dell-only commit option cannot leave attributes staged on iLO."""
    mgr, svc = redfish_mock_factory("hpe")
    with pytest.raises(UnsupportedAction):
        mgr.sync_invoke(
            ApiRequestType.BiosChangeSettings, "bios_change_settings",
            from_spec=bios_spec, **{option: True},
        )
    assert all(r.method == "GET" for r in svc.mocker.request_history)


@pytest.mark.parametrize("failure", ["missing", "malformed", "encoded-traversal", "forbidden"])
def test_bios_change_requires_advertised_settings_on_ilo(
    redfish_mock_factory, bios_spec: str, failure: str,
) -> None:
    """An unusable SettingsObject never falls back to an invented PATCH URI."""
    mgr, svc = redfish_mock_factory("hpe")
    bios = deepcopy(svc._state(_ILO_BIOS_URI))
    status = 200
    expected_error = UnsupportedAction
    if failure == "missing":
        del bios["@Redfish.Settings"]["SettingsObject"]
    elif failure == "malformed":
        bios["@Redfish.Settings"]["SettingsObject"]["@odata.id"] = "Bios/Settings"
    elif failure == "encoded-traversal":
        bios["@Redfish.Settings"]["SettingsObject"]["@odata.id"] = (
            f"{_ILO_BIOS_URI}/%252e%252e/Settings"
        )
    else:
        status = 403
        expected_error = RedfishForbidden
    svc.mocker.get(_ILO_BIOS_URI, json=bios, status_code=status)

    with pytest.raises(expected_error):
        mgr.sync_invoke(
            ApiRequestType.BiosChangeSettings, "bios_change_settings",
            from_spec=bios_spec,
        )
    assert all(r.method == "GET" for r in svc.mocker.request_history)


def test_bios_change_returns_rejected_patch_without_reboot(
    redfish_mock_factory, bios_spec: str,
) -> None:
    """A non-raising PATCH conflict is returned without a follow-up reboot."""
    mgr, svc = redfish_mock_factory("hpe")
    message = "BIOS settings are locked by a pending operation."
    svc.mocker.patch(
        _ILO_SETTINGS_URI, status_code=409,
        json={"error": {"code": "Base.1.0.GeneralError", "message": message}},
    )
    result = mgr.sync_invoke(
        ApiRequestType.BiosChangeSettings, "bios_change_settings",
        from_spec=bios_spec, do_reboot=True,
    )
    assert message in str(result.error)
    assert svc.mocker.last_request.method == "PATCH"
    assert svc.mocker.last_request.path == _ILO_SETTINGS_URI


def test_bios_registry_uses_standard_registry_on_ilo(redfish_mock_factory) -> None:
    """Registry readback uses the BIOS AttributeRegistry without Dell probing."""
    mgr, svc = redfish_mock_factory("hpe")
    result = mgr.sync_invoke(
        ApiRequestType.BiosRegistry, "bios_registry",
        attr_name="DynamicPowerCapping", is_value_only=False,
    )
    assert result.error is None
    assert [entry["AttributeName"] for entry in result.data] == ["DynamicPowerCapping"]
    assert all(not r.path.lower().endswith("/bios/biosregistry")
               for r in svc.mocker.request_history)


@pytest.mark.parametrize("option", ["do_commit", "commit_pending"])
def test_bios_change_dispatches_dell_apply_at_requested_stage(
    redfish_mock, redfish_service, bios_spec: str, option: str,
) -> None:
    """Dell's OEM job applies before or after staging according to the flag."""
    pending = {"DynamicPowerCapping": "Disabled"} if option == "commit_pending" else {}
    redfish_service.mocker.get(_DELL_SETTINGS_URI, json={"Attributes": pending})

    def apply_job(_job: JobApply, *, do_reboot: bool, do_watch: bool,
                  setting: str = "bios") -> CommandResult:
        """Record the OEM job dispatch while keeping its side effects offline."""
        assert do_reboot and do_watch
        assert setting == "bios"
        patch_already_sent = any(
            r.method == "PATCH" for r in redfish_service.mocker.request_history
        )
        assert patch_already_sent == (option == "do_commit")
        return CommandResult({}, None, None, None)

    with patch.object(JobApply, "execute", autospec=True, side_effect=apply_job) as apply:
        result = redfish_mock.sync_invoke(
            ApiRequestType.BiosChangeSettings, "bios_change_settings",
            from_spec=bios_spec, **{option: True},
        )
    apply.assert_called_once()
    assert result.error is None
    writes = [(r.method, urlsplit(r.url).path) for r in redfish_service.mocker.request_history
              if r.method != "GET"]
    assert writes == [("PATCH", _DELL_SETTINGS_URI)]


def test_bios_change_still_works_on_dell(redfish_mock):
    """Dell path unchanged: --do_show builds the same pending payload."""
    result = redfish_mock.sync_invoke(ApiRequestType.BiosChangeSettings, "bios_change_settings",
                                      attr_name="DynamicPowerCapping", attr_value="Enabled",
                                      do_show=True)
    assert result.data.get("Attributes", {}).get("DynamicPowerCapping") == "Enabled"
