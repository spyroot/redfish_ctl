"""Dual-mode tests for Dell persistent-storage media initialization."""

from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

PERSISTENT_STORAGE = (
    "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellPersistentStorageService"
)
INITIALIZE_TARGET = (
    f"{PERSISTENT_STORAGE}/Actions/"
    "DellPersistentStorageService.InitializeMedia"
)


def _post_requests(redfish_service):
    """Return POST requests recorded by the mock Redfish service.

    :param redfish_service: the mock service fixture.
    :return: list of recorded POST requests.
    """
    return [
        request for request in redfish_service.requests
        if request.method == "POST"
    ]


def test_dell_persistent_initialize_media_previews_by_default(
    redfish_mock,
    redfish_service,
):
    """The media initialization command resolves the target without POSTing."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.DellPersistentInitializeMedia,
        "dell-persistent-initialize-media",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == "#DellPersistentStorageService.InitializeMedia"
    assert result.data["target"] == INITIALIZE_TARGET
    assert result.data["payload"] == {}
    assert result.data["level"] == "irreversible"
    assert result.data["blocked"] == (
        "irreversible action requires --confirm and --i-understand-irreversible"
    )
    assert _post_requests(redfish_service) == []


def test_dell_persistent_initialize_media_confirm_only_stays_blocked(
    redfish_mock,
    redfish_service,
):
    """The irreversible guard blocks confirm-only initialization attempts."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.DellPersistentInitializeMedia,
        "dell-persistent-initialize-media",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["level"] == "irreversible"
    assert result.data["blocked"] == (
        "irreversible action requires --confirm and --i-understand-irreversible"
    )
    assert _post_requests(redfish_service) == []


def test_dell_persistent_initialize_media_posts_with_irreversible_consent(
    redfish_mock,
    redfish_service,
):
    """The command POSTs an empty body only with the irreversible confirmation."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.DellPersistentInitializeMedia,
        "dell-persistent-initialize-media",
        confirm=True,
        confirm_irreversible=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["task_id"] == redfish_service.JOB_ID
    assert result.data["executed"] is True
    assert result.data["action"] == "#DellPersistentStorageService.InitializeMedia"
    assert result.data["target"] == INITIALIZE_TARGET
    assert result.data["level"] == "irreversible"
    posts = _post_requests(redfish_service)
    assert len(posts) == 1
    assert posts[0].path.lower() == INITIALIZE_TARGET.lower()
    assert posts[0].json() == {}
