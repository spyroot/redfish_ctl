"""Offline tests for Dell OS deployment network ISO actions."""
import json
import tarfile
from pathlib import Path

from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.redfish_manager_shared import ApiRequestType


CORPUS = Path(__file__).with_name("dell_xr8620t_corpus.tar.gz")
CORPUS_MEMBER = (
    "10.252.252.209/"
    "_redfish_v1_Systems_System.Embedded.1_Oem_Dell_DellOSDeploymentService.json"
)
SERVICE_URI = "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellOSDeploymentService"
ACTION_PREFIX = f"{SERVICE_URI}/Actions/DellOSDeploymentService"


def _load_corpus_service():
    with tarfile.open(CORPUS, "r:gz") as archive:
        member = archive.extractfile(CORPUS_MEMBER)
        assert member is not None
        return json.loads(member.read().decode("utf-8"))


def _seed_corpus_service(redfish_service):
    service = _load_corpus_service()
    redfish_service._overlay[SERVICE_URI] = service
    redfish_service._overlay[SERVICE_URI.lower()] = service
    return service


def _post_count(redfish_service):
    return sum(1 for request in redfish_service.requests if request.method == "POST")


def test_dell_os_network_iso_actions_list_corpus_targets(
    redfish_mock,
    redfish_service,
):
    """The command lists the network ISO actions advertised by the XR8620t corpus."""
    _seed_corpus_service(redfish_service)

    result = redfish_mock.sync_invoke(
        ApiRequestType.DellOsNetworkIsoActions,
        "dell-os-network-iso-actions",
    )

    assert isinstance(result, CommandResult)
    rows = result.data["os_deployment_targets"]
    assert len(rows) == 1
    actions = {row["Action"]: row for row in rows[0]["Actions"]}
    assert {
        "configurable-boot-network-iso",
        "download-iso-to-vflash",
        "unpack-and-attach",
        "unpack-and-share",
    }.issubset(actions)
    assert actions["download-iso-to-vflash"]["AllowableValues"]["ShareType"] == [
        "CIFS",
        "NFS",
        "TFTP",
    ]
    assert actions["configurable-boot-network-iso"]["AllowableValues"]["ResetType"] == [
        "ColdReset",
        "NoReset",
        "WarmReset",
    ]


def test_configurable_boot_to_network_iso_dry_run_redacts_password(
    redfish_mock,
    redfish_service,
    monkeypatch,
):
    """Selected network ISO actions preview by default and mask share passwords."""
    _seed_corpus_service(redfish_service)
    monkeypatch.setenv("ISO_SHARE_PASSWORD", "Secret123!")

    result = redfish_mock.sync_invoke(
        ApiRequestType.DellOsNetworkIsoActions,
        "dell-os-network-iso-actions",
        action="configurable-boot-network-iso",
        ip_addr="192.0.2.10",
        share_type="CIFS",
        share_name="install-media",
        image_name="ubuntu.iso",
        share_username="media-user",
        share_password_env="ISO_SHARE_PASSWORD",
        workgroup="LAB",
        hash_type="SHA1",
        image_hash_value="abc123",
        reset_type="NoReset",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == (
        "#DellOSDeploymentService.ConfigurableBootToNetworkISO"
    )
    assert result.data["target"] == f"{ACTION_PREFIX}.ConfigurableBootToNetworkISO"
    assert result.data["level"] == "destructive"
    assert result.data["blocked"] == "destructive action requires --confirm"
    assert result.data["payload"] == {
        "IPAddress": "192.0.2.10",
        "ShareType": "CIFS",
        "ShareName": "install-media",
        "ImageName": "ubuntu.iso",
        "UserName": "media-user",
        "Password": "********",
        "Workgroup": "LAB",
        "HashType": "SHA1",
        "ImageHashValue": "abc123",
        "ResetType": "NoReset",
    }
    assert _post_count(redfish_service) == 0


def test_download_iso_to_vflash_confirm_posts_discovered_target(
    redfish_mock,
    redfish_service,
    monkeypatch,
):
    """With --confirm, the command POSTs the real corpus target and payload."""
    _seed_corpus_service(redfish_service)
    monkeypatch.setenv("ISO_SHARE_PASSWORD", "Secret123!")

    result = redfish_mock.sync_invoke(
        ApiRequestType.DellOsNetworkIsoActions,
        "dell-os-network-iso-actions",
        action="download-iso-to-vflash",
        ip_addr="192.0.2.20",
        share_type="TFTP",
        share_name="/isos",
        image_name="rhel.iso",
        share_password_env="ISO_SHARE_PASSWORD",
        hash_type="MD5",
        image_hash_value="feedface",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#DellOSDeploymentService.DownloadISOToVFlash"
    assert result.data["target"] == f"{ACTION_PREFIX}.DownloadISOToVFlash"
    assert redfish_service.last_request.method == "POST"
    assert redfish_service.last_request.path == (
        f"{ACTION_PREFIX}.DownloadISOToVFlash"
    ).lower()
    assert redfish_service.last_request.json() == {
        "IPAddress": "192.0.2.20",
        "ShareType": "TFTP",
        "ShareName": "/isos",
        "ImageName": "rhel.iso",
        "Password": "Secret123!",
        "HashType": "MD5",
        "ImageHashValue": "feedface",
    }


def test_invalid_share_type_is_rejected_before_post(
    redfish_mock,
    redfish_service,
):
    """AllowableValues metadata rejects unsupported ShareType values with no POST."""
    _seed_corpus_service(redfish_service)

    result = redfish_mock.sync_invoke(
        ApiRequestType.DellOsNetworkIsoActions,
        "dell-os-network-iso-actions",
        action="download-iso-to-vflash",
        share_type="HTTP",
    )

    assert isinstance(result, CommandResult)
    assert "invalid value" in result.error
    assert result.data["validation_errors"] == [
        {
            "parameter": "ShareType",
            "value": "HTTP",
            "allowed": ["CIFS", "NFS", "TFTP"],
        }
    ]
    assert _post_count(redfish_service) == 0
