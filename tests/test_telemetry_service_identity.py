"""OTel producer identity validation, discovery, and backend projection."""

from __future__ import annotations

import json
import tarfile
import uuid
from pathlib import Path

import pytest

from redfish_ctl.config import ConfigurationConflict
from redfish_ctl.redfish_manager import RedfishResponseCache
from redfish_ctl.telemetry import identity
from redfish_ctl.telemetry.cmd_exporter import Exporter
from redfish_ctl.telemetry.exporter import (
    MetricSample,
    render_prometheus_text,
    to_signalfx_body,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

CORPUS_MANAGERS = (
    (
        "dell_xr8620t_corpus.tar.gz",
        "10.252.252.209/_redfish_v1_Managers_iDRAC.Embedded.1.json",
    ),
    (
        "hpe_dl360_corpus.tar.gz",
        "10.43.3.209/_redfish_v1_Managers_1.json",
    ),
    (
        "supermicro_x10_corpus.tar.gz",
        "192.168.254.120/_redfish_v1_Managers_1.json",
    ),
    (
        "supermicro_gb300_corpus.tar.gz",
        "172.25.230.37/_redfish_v1_Managers_BMC_0.json",
    ),
    (
        "nvidia_gb300_node2_corpus.tar.gz",
        "172.25.230.20/_redfish_v1_Managers_BMC_0.json",
    ),
)


@pytest.fixture(autouse=True)
def _clear_service_identity_env(monkeypatch):
    """Keep producer identity environment settings isolated per test.

    :param monkeypatch: pytest environment patcher.
    """
    suffixes = (
        "SERVICE_NAME",
        "SERVICE_NAMESPACE",
        "SERVICE_INSTANCE_ID",
        "SERVICE_VERSION",
        "SERVICE_CRITICALITY",
    )
    for prefix in ("REDFISH_EXPORTER_", "IDRAC_EXPORTER_"):
        for suffix in suffixes:
            monkeypatch.delenv(prefix + suffix, raising=False)


def _telemetry_identity(**kwargs) -> identity.TelemetryIdentity:
    """Return a minimal producer identity with selected overrides.

    :param kwargs: TelemetryIdentity fields to override.
    :return: validated telemetry identity.
    """
    fields = dict(host_name="host", node="node", server_address="server", bmc_ip="bmc")
    fields.update(kwargs)
    return identity.TelemetryIdentity(**fields)


def _archive_json(archive_name: str, member_name: str) -> dict:
    """Read one JSON resource directly from a committed corpus archive.

    :param archive_name: tarball filename under tests/.
    :param member_name: flattened resource path inside the tarball.
    :return: parsed resource mapping.
    """
    with tarfile.open(REPO_ROOT / "tests" / archive_name) as archive:
        member = archive.extractfile(member_name)
        assert member is not None
        return json.load(member)


def test_optional_service_identity_is_resource_only_on_metric_backends():
    """Optional service fields reach resources without minting labels or dimensions."""
    producer = _telemetry_identity(
        service_namespace="hardware",
        service_instance_id="rack-a-exporter",
        service_version="2.0.0",
        service_criticality="critical",
    )
    attrs = producer.resource_attributes()
    assert attrs["service.namespace"] == "hardware"
    assert attrs["service.version"] == "2.0.0"
    assert attrs["service.criticality"] == "critical"
    assert uuid.UUID(attrs["service.instance.id"])

    sample = MetricSample("hw.power", 1.0, producer.dimensions(), unit="W")
    prometheus = render_prometheus_text([sample])
    signalfx = to_signalfx_body([sample])["gauge"][0]["dimensions"]
    readback = identity.common_sample_dimensions([sample])
    for key in identity.RESOURCE_ONLY_DIMENSIONS:
        assert key not in prometheus
        assert key not in signalfx
        assert key not in readback


def test_optional_service_identity_is_omitted_when_unset():
    """Unset optional service fields do not appear in resource attributes."""
    attrs = _telemetry_identity().resource_attributes()
    assert not (set(identity.RESOURCE_ONLY_DIMENSIONS) & set(attrs))


@pytest.mark.parametrize("value", ["has space", "line\nbreak", "ghp_deadbeef"])
def test_service_identity_rejects_whitespace_control_and_secret_shapes(value):
    """Producer identity values reject unsafe or non-OTel-conformant text."""
    with pytest.raises(ValueError):
        _telemetry_identity(service_namespace=value)


@pytest.mark.parametrize("value", ["3d-render", "_internal"])
def test_service_name_allows_otel_free_form_prefixes(value):
    """OTel service.name does not require a leading ASCII letter."""
    assert _telemetry_identity(service_name=value).service_name == value


def test_service_identity_keys_are_reserved_for_dedicated_options():
    """Generic dimensions cannot override semantic service identity fields."""
    for key in identity.RESOURCE_ONLY_DIMENSIONS:
        with pytest.raises(ValueError, match="reserved"):
            identity.parse_dimension_pairs([f"{key}=value"])


def test_service_instance_id_preserves_uuid_and_wraps_raw_token():
    """Caller UUIDs remain stable while raw tokens map to the fixed UUIDv5 namespace."""
    supplied = "cb0377f1-e3b9-4da9-9275-71825b2c6434"
    assert identity.canonical_service_instance_id(supplied) == supplied
    assert identity.canonical_service_instance_id("rack-a-exporter") == str(
        uuid.uuid5(identity.SERVICE_INSTANCE_NAMESPACE, "rack-a-exporter"))


def test_service_instance_source_precedence_skips_placeholders_and_local_macs():
    """Discovery skips placeholders before selecting the first stable source class."""
    result = identity.service_instance_id_from_sources(
        manager_uuids=["00000000-0000-0000-0000-000000000000"],
        chassis_serials=["To be filled by O.E.M.", "HA256S016142"],
        permanent_macs=["02:00:00:00:00:01"],
        mac_addresses=["7C:A6:2A:40:C3:E5"],
    )
    assert result == str(uuid.uuid5(
        identity.SERVICE_INSTANCE_NAMESPACE,
        "HA256S016142",
    ))


@pytest.mark.parametrize(("archive_name", "member_name"), CORPUS_MANAGERS)
def test_service_instance_default_uses_manager_uuid_from_each_vendor_corpus(
        archive_name, member_name):
    """All five committed vendor corpora expose a usable Manager UUID."""
    manager = _archive_json(archive_name, member_name)
    raw_uuid = manager["UUID"]
    assert identity.service_instance_id_from_sources([raw_uuid]) == str(
        uuid.uuid5(identity.SERVICE_INSTANCE_NAMESPACE, str(uuid.UUID(raw_uuid))))


@pytest.mark.parametrize("do_async", [False, True])
def test_exporter_discovers_instance_id_in_sync_and_async_modes(do_async):
    """Exporter discovery uses the same source precedence in both query modes."""
    resources = {
        "/redfish/v1/Managers": {
            "Members": [{"@odata.id": "/redfish/v1/Managers/BMC"}],
        },
        "/redfish/v1/Managers/BMC": {
            "UUID": "00000000-0000-0000-0000-000000000000",
            "EthernetInterfaces": {
                "@odata.id": "/redfish/v1/Managers/BMC/EthernetInterfaces",
            },
        },
        "/redfish/v1/Managers/BMC/EthernetInterfaces": {
            "Members": [{
                "@odata.id": "/redfish/v1/Managers/BMC/EthernetInterfaces/1",
            }],
        },
        "/redfish/v1/Managers/BMC/EthernetInterfaces/1": {
            "PermanentMACAddress": "7C:A6:2A:40:C3:E5",
        },
        "/redfish/v1/Chassis": {
            "Members": [{"@odata.id": "/redfish/v1/Chassis/BMC_0"}],
        },
        "/redfish/v1/Chassis/BMC_0": {
            "Id": "BMC_0",
            "ChassisType": "Module",
            "SerialNumber": "HA256S016142",
        },
    }
    observed_modes = []

    def resource(_self, uri, _cache, selected_mode):
        observed_modes.append(selected_mode)
        return resources.get(uri, {})

    class DiscoveryHarness:
        """Bind the pure discovery walk without constructing a Redfish client."""

        _members = staticmethod(Exporter._members)
        _chassis_identity_rank = staticmethod(Exporter._chassis_identity_rank)
        _identity_resource = resource
        _discover_service_instance_id = Exporter._discover_service_instance_id

    command = DiscoveryHarness()
    result = command._discover_service_instance_id(
        RedfishResponseCache(),
        do_async=do_async,
    )
    assert result == str(uuid.uuid5(
        identity.SERVICE_INSTANCE_NAMESPACE,
        "HA256S016142",
    ))
    assert observed_modes and set(observed_modes) == {do_async}


def test_service_identity_env_alias_conflict_fails_closed(monkeypatch):
    """Conflicting canonical and deprecated service identity names are rejected."""
    monkeypatch.setenv("REDFISH_EXPORTER_SERVICE_NAMESPACE", "fleet-a")
    monkeypatch.setenv("IDRAC_EXPORTER_SERVICE_NAMESPACE", "fleet-b")
    with pytest.raises(ConfigurationConflict):
        identity.resolve_identity_options()


def test_explicit_service_identity_overrides_conflicting_env_aliases(monkeypatch):
    """An explicit CLI/config value wins over conflicting environment aliases."""
    monkeypatch.setenv("REDFISH_EXPORTER_SERVICE_NAMESPACE", "fleet-a")
    monkeypatch.setenv("IDRAC_EXPORTER_SERVICE_NAMESPACE", "fleet-b")
    resolved = identity.resolve_identity_options(service_namespace="fleet-cli")
    assert resolved["service_namespace"] == "fleet-cli"
