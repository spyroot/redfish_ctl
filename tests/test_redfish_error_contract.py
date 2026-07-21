"""Focused tests for generic DMTF Redfish error handling.

See docs/external/redfish-error-contract.md for the binding protocol contract.
"""

import argparse
import ast
import json
import tarfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from redfish_ctl.cmd_exceptions import (
    AuthenticationFailed,
    ResourceNotFound,
    UnexpectedResponse,
)
from redfish_ctl.redfish_exceptions import RedfishForbidden
from redfish_ctl.redfish_main import json_printer
from redfish_ctl.redfish_manager import RedfishManager
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_respond_error import RedfishError

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "redfish_ctl"
CONTRACT_DOC = REPO_ROOT / "docs" / "external" / "redfish-error-contract.md"
DMTF_2026_1_ROOT = REPO_ROOT / "spec" / "dmtf" / "redfish" / "2026.1"
DMTF_2026_1_MANIFEST = DMTF_2026_1_ROOT / "manifest.yaml"
DSP8010_2026_1_SCHEMA_BUNDLE = (
    DMTF_2026_1_ROOT / "schemas" / "DSP8010_2026.1.zip"
)
DSP2043_2026_1_MOCKUPS_BUNDLE = (
    DMTF_2026_1_ROOT / "mockups" / "DSP2043_2026.1.zip"
)
DELL_FULL_CORPUS = REPO_ROOT / "full_corpus" / "dell_xr8620t_full_corpus.tar.gz"
GB300_FULL_CORPUS = REPO_ROOT / "full_corpus" / "supermicro_gb300_full_corpus.tar.gz"


_EXTENDED_INFO = [
    {
        "MessageId": "Base.1.18.ResourceMissingAtURI",
        "Message": "The resource at the URI /redfish/v1/Managers/1/VM1 was not found.",
        "MessageArgs": ["/redfish/v1/Managers/1/VM1"],
        "MessageSeverity": "Warning",
        "Resolution": "Use a supported Redfish or OEM virtual-media endpoint.",
    }
]

_DMTF_ERROR_BODY = {
    "error": {
        "code": "Base.1.18.GeneralError",
        "message": "Standard VirtualMedia is not implemented on this BMC.",
        "@Message.ExtendedInfo": _EXTENDED_INFO,
    }
}


def _args(**kw):
    base = dict(no_stdout=False, json_only=True, yaml=False)
    base.update(kw)
    return argparse.Namespace(**base)


def _corpus_json(tarball, member):
    with tarfile.open(tarball, "r:gz") as archive:
        stream = archive.extractfile(member)
        assert stream is not None, f"{member} missing from {tarball}"
        return json.loads(stream.read().decode("utf-8"))


def _zip_names(bundle):
    with zipfile.ZipFile(bundle) as archive:
        return set(archive.namelist())


def _assert_location_points_to_dmtf(descriptor, expected_publication_uri, expected_uri):
    locations = descriptor.get("Location")
    assert isinstance(locations, list)
    assert locations
    location = locations[0]
    assert location["PublicationUri"] == expected_publication_uri
    assert location["Uri"] == expected_uri


def _assert_redfish_schema_file(
    descriptor,
    *,
    expected_odata_id,
    expected_schema,
    expected_publication_uri,
    expected_uri,
):
    assert descriptor["@odata.id"] == expected_odata_id
    assert descriptor["@odata.type"].startswith("#JsonSchemaFile.")
    assert descriptor["Schema"] == expected_schema
    _assert_location_points_to_dmtf(
        descriptor,
        expected_publication_uri,
        expected_uri,
    )


class _Response:
    """Minimal response double for offline Redfish error parsing."""

    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


@pytest.mark.parametrize(
    ("message_extended", "expected_fragment"),
    [
        (
            [
                {
                    "Message": None,
                    "MessageId": "Base.1.18.ActionNotSupported",
                    "MessageArgs": ["VirtualMedia"],
                }
            ],
            "ActionNotSupported",
        ),
        (
            [
                {
                    "MessageId": "Base.1.18.ActionNotSupported",
                    "MessageArgs": ["VirtualMedia"],
                }
            ],
            "ActionNotSupported",
        ),
        (
            [
                SimpleNamespace(
                    message=None,
                    message_id="Base.1.18.ActionNotSupported",
                    message_args=["VirtualMedia"],
                )
            ],
            "ActionNotSupported",
        ),
        (
            [SimpleNamespace(message={"raw": "dict message"}, message_id="", message_args=[])],
            "dict message",
        ),
    ],
)
def test_redfish_error_str_never_raises_for_dmtf_extended_info_shapes(
    message_extended,
    expected_fragment,
):
    """Missing/null Message and raw dict/object ExtendedInfo entries stay printable."""
    error = RedfishError(501, message=None)
    error.message_extended = message_extended

    rendered = str(error)

    assert "HTTP 501" in rendered
    assert expected_fragment in rendered


def test_parse_error_preserves_dmtf_error_envelope_fields_and_status():
    """DMTF error.code/message/ExtendedInfo stay attached to the RedfishError."""
    parsed = RedfishManager.parse_error(_Response(501, _DMTF_ERROR_BODY))

    assert parsed.status_code == 501
    assert parsed.code == "Base.1.18.GeneralError"
    assert parsed.message == "Standard VirtualMedia is not implemented on this BMC."
    assert parsed.message_extended == _EXTENDED_INFO


def test_dell_full_corpus_exposes_dmtf_error_schema_and_registry_contract():
    """Dell iDRAC publishes DMTF resource/error schema and registry pointers."""
    resource_schema = _corpus_json(
        DELL_FULL_CORPUS,
        "10.252.252.209/_redfish_v1_JsonSchemas_Resource.json",
    )
    error_schema = _corpus_json(
        DELL_FULL_CORPUS,
        "10.252.252.209/_redfish_v1_JsonSchemas_RedfishError.v1_0_1.json",
    )
    message_schema = _corpus_json(
        DELL_FULL_CORPUS,
        "10.252.252.209/_redfish_v1_JsonSchemas_Message.json",
    )
    base_messages = _corpus_json(
        DELL_FULL_CORPUS,
        "10.252.252.209/_redfish_v1_Registries_BaseMessages.json",
    )

    _assert_redfish_schema_file(
        resource_schema,
        expected_odata_id="/redfish/v1/JsonSchemas/Resource",
        expected_schema="#Resource.Resource",
        expected_publication_uri="http://redfish.dmtf.org/schemas/v1/Resource.json",
        expected_uri="/redfish/v1/Schemas/Resource.json",
    )
    _assert_redfish_schema_file(
        error_schema,
        expected_odata_id="/redfish/v1/JsonSchemas/RedfishError.v1_0_1",
        expected_schema="#RedfishError.v1_0_1.RedfishError",
        expected_publication_uri=(
            "http://redfish.dmtf.org/schemas/v1/RedfishError.v1_0_1.json"
        ),
        expected_uri="/redfish/v1/Schemas/RedfishError.v1_0_1.json",
    )
    _assert_redfish_schema_file(
        message_schema,
        expected_odata_id="/redfish/v1/JsonSchemas/Message",
        expected_schema="#Message.Message",
        expected_publication_uri="http://redfish.dmtf.org/schemas/v1/Message.json",
        expected_uri="/redfish/v1/Schemas/Message.json",
    )
    assert base_messages["@odata.id"] == "/redfish/v1/Registries/BaseMessages"
    assert base_messages["@odata.type"].startswith("#MessageRegistryFile.")
    assert base_messages["Registry"] == "Base.1.12.1"
    _assert_location_points_to_dmtf(
        base_messages,
        "https://redfish.dmtf.org/registries/v1/Base.1.12.1.json",
        "/redfish/v1/Registries/BaseMessages/BaseRegistry.json",
    )


def test_gb300_full_corpus_exposes_dmtf_error_schema_and_registry_contract():
    """GB300 publishes DMTF resource/error schema and registry pointers."""
    resource_schema = _corpus_json(
        GB300_FULL_CORPUS,
        "172.25.230.37/_redfish_v1_JsonSchemas_Resource.json",
    )
    error_schema = _corpus_json(
        GB300_FULL_CORPUS,
        "172.25.230.37/_redfish_v1_JsonSchemas_redfish-error.json",
    )
    message_schema = _corpus_json(
        GB300_FULL_CORPUS,
        "172.25.230.37/_redfish_v1_JsonSchemas_Message.json",
    )
    base_registry = _corpus_json(
        GB300_FULL_CORPUS,
        "172.25.230.37/_redfish_v1_Registries_Base.json",
    )

    _assert_redfish_schema_file(
        resource_schema,
        expected_odata_id="/redfish/v1/JsonSchemas/Resource",
        expected_schema="#Resource.Resource",
        expected_publication_uri=(
            "http://redfish.dmtf.org/schemas/v1/Resource.v1_19_0.json"
        ),
        expected_uri="/redfish/v1/JsonSchemas/Resource/Resource.v1_19_0.json",
    )
    _assert_redfish_schema_file(
        error_schema,
        expected_odata_id="/redfish/v1/JsonSchemas/redfish-error",
        expected_schema="#redfish-error.redfish-error",
        expected_publication_uri=(
            "http://redfish.dmtf.org/schemas/v1/redfish-error.v1_0_2.json"
        ),
        expected_uri="/redfish/v1/JsonSchemas/redfish-error/redfish-error.v1_0_2.json",
    )
    _assert_redfish_schema_file(
        message_schema,
        expected_odata_id="/redfish/v1/JsonSchemas/Message",
        expected_schema="#Message.Message",
        expected_publication_uri=(
            "http://redfish.dmtf.org/schemas/v1/Message.v1_2_1.json"
        ),
        expected_uri="/redfish/v1/JsonSchemas/Message/Message.v1_2_1.json",
    )
    assert base_registry["@odata.id"] == "/redfish/v1/Registries/Base"
    assert base_registry["@odata.type"].startswith("#MessageRegistryFile.")
    assert base_registry["Registry"] == "Base.1.18.1"
    _assert_location_points_to_dmtf(
        base_registry,
        "https://redfish.dmtf.org/registries/Base.1.18.1.json",
        "/redfish/v1/Registries/Base/Base",
    )


def test_dmtf_2026_1_release_manifest_names_required_contracts():
    """The local DMTF release manifest binds docs, gates, and automation rules."""
    manifest = yaml.safe_load(DMTF_2026_1_MANIFEST.read_text(encoding="utf-8"))

    assert manifest["metadata"]["release"] == "2026.1"
    assert manifest["metadata"]["publicationDate"] == "2026-05-17"
    artifacts = {artifact["id"]: artifact for artifact in manifest["artifacts"]}
    expected_artifacts = {
        "DSP0266",
        "DSP0268",
        "DSP0272",
        "DSP2043",
        "DSP2046",
        "DSP2053",
        "DSP2065",
        "DSP8010",
        "DSP8011",
        "DSP8013",
    }
    assert expected_artifacts.issubset(artifacts)

    assert artifacts["DSP8010"]["localPath"] == "schemas/DSP8010_2026.1.zip"
    assert artifacts["DSP8010"]["gitStorage"] == "git-lfs"
    assert artifacts["DSP2043"]["localPath"] == "mockups/DSP2043_2026.1.zip"
    assert artifacts["DSP2043"]["gitStorage"] == "normal-git"
    assert artifacts["DSP8011"]["localRequiredWhen"]

    contracts = {contract["id"]: contract for contract in manifest["contracts"]}
    assert {
        "error-envelope-normalization",
        "schema-pointer-compatibility",
        "simulator-corpus-baseline",
    }.issubset(contracts)
    assert "DSP8011" in contracts["error-envelope-normalization"]["authority"]

    gates = {gate["id"]: gate for gate in manifest["gates"]}
    assert {
        "dmtf-release-manifest",
        "dmtf-schema-bundle",
        "dmtf-mockup-bundle",
    }.issubset(gates)
    assert any(
        "Do not invent a Redfish error shape" in rule
        for rule in manifest["automationRules"]
    )

    for artifact in artifacts.values():
        local_path = artifact.get("localPath")
        if artifact.get("localRequired"):
            assert local_path
            assert (DMTF_2026_1_ROOT / local_path).exists()


def test_dsp8010_2026_1_schema_bundle_contains_required_error_artifacts():
    """The pinned DSP8010 bundle carries schema files needed by the contract."""
    names = _zip_names(DSP8010_2026_1_SCHEMA_BUNDLE)
    expected_members = {
        "DSP8010_2026.1/info.json",
        "DSP8010_2026.1/DSP0268_2026.1.html",
        "DSP8010_2026.1/DSP2046_2026.1.html",
        "DSP8010_2026.1/DSP2053_2026.1.html",
        "DSP8010_2026.1/DSP8010_2026.1.html",
        "DSP8010_2026.1/json-schema/Resource.json",
        "DSP8010_2026.1/json-schema/Resource.v1_19_0.json",
        "DSP8010_2026.1/json-schema/Message.json",
        "DSP8010_2026.1/json-schema/Message.v1_2_1.json",
        "DSP8010_2026.1/json-schema/redfish-error.v1_0_2.json",
        "DSP8010_2026.1/csdl/RedfishError_v1.xml",
        "DSP8010_2026.1/csdl/AccelerationFunction_v1.xml",
        "DSP8010_2026.1/openapi/AccelerationFunction.yaml",
    }
    assert expected_members.issubset(names)

    with zipfile.ZipFile(DSP8010_2026_1_SCHEMA_BUNDLE) as archive:
        info = json.loads(archive.read("DSP8010_2026.1/info.json"))
    assert info == {"version": "2026.1", "date": "2026-04-02"}


def test_dsp2043_2026_1_mockups_bundle_contains_simulator_seed_payloads():
    """The pinned DSP2043 bundle carries DMTF mockups for simulator seeding."""
    names = _zip_names(DSP2043_2026_1_MOCKUPS_BUNDLE)
    expected_members = {
        "DSP2043_2026.1/public-rackmount1/index.json",
        "DSP2043_2026.1/public-rackmount1/Systems/index.json",
        "DSP2043_2026.1/public-rackmount1/Managers/index.json",
        "DSP2043_2026.1/public-applications/Systems/VM1/index.json",
        "DSP2043_2026.1/public-applications/Managers/BMC/index.json",
        "DSP2043_2026.1/public-applications/AccountService/index.json",
        "DSP2043_2026.1/public-bladed/Chassis/index.json",
        "DSP2043_2026.1/DSP2046-examples/ServiceRoot-v1-example.json",
    }
    assert expected_members.issubset(names)


def test_contract_doc_names_dmtf_publication_surfaces():
    """The written contract stays bound to DMTF schema and registry surfaces."""
    text = CONTRACT_DOC.read_text(encoding="utf-8")

    assert "DSP8010_2025.4.zip" in text
    assert "DSP8010_2026.1.zip" in text
    assert "DSP2043_2026.1.zip" in text
    assert "DSP8011_2026.1.zip" in text
    assert "https://redfish.dmtf.org/redfish/schema_index" in text
    assert "https://redfish.dmtf.org/schemas/v1/" in text
    assert "https://redfish.dmtf.org/schemas/v1/AccelerationFunction_v1.xml" in text
    assert "https://redfish.dmtf.org/schemas/v1/AccelerationFunction.v1_0_5.json" in text
    assert "https://redfish.dmtf.org/schemas/v1/AccelerationFunction.yaml" in text
    assert "https://redfish.dmtf.org/registries/" in text


def test_default_error_handler_raises_resource_not_found_with_parsed_dmtf_error():
    """Default non-2xx handling surfaces the parsed RedfishError, not a generic string."""
    with pytest.raises(ResourceNotFound) as raised:
        RedfishManager.default_error_handler(_Response(501, _DMTF_ERROR_BODY))

    parsed = raised.value.args[0]
    assert isinstance(parsed, RedfishError)
    assert parsed.status_code == 501
    assert parsed.code == "Base.1.18.GeneralError"
    assert parsed.message == "Standard VirtualMedia is not implemented on this BMC."
    assert parsed.message_extended == _EXTENDED_INFO


def _base_manager():
    """Return a RedfishManagerBase instance — the class every command subclasses.

    :return: an offline RedfishManagerBase (no BMC contact).
    """
    return RedfishManagerBase(
        idrac_ip="mock", idrac_username="root", idrac_password="x",
        insecure=True, is_debug=False)


@pytest.mark.parametrize(
    ("status_code", "exc_type"),
    [
        (401, AuthenticationFailed),
        (403, RedfishForbidden),
        (404, ResourceNotFound),
        (405, UnexpectedResponse),
        (409, UnexpectedResponse),
        (500, UnexpectedResponse),
        (501, UnexpectedResponse),
        (502, UnexpectedResponse),
        (503, UnexpectedResponse),
    ],
)
def test_base_default_error_handler_preserves_dmtf_envelope(status_code, exc_type):
    """Every command subclasses RedfishManagerBase, so ITS default_error_handler —
    not the parent RedfishManager's — is the real command error path. For every
    error code it must raise the parsed RedfishError envelope (status, error.code,
    every @Message.ExtendedInfo), never a generic string, per the Redfish error
    contract. Regression: the base override previously raised the generic
    "Failed acquire result. Status code N" for 501/5xx, defeating the contract that
    the parent-only test could not see.
    """
    manager = _base_manager()
    with pytest.raises(exc_type) as raised:
        manager.default_error_handler(_Response(status_code, _DMTF_ERROR_BODY))
    parsed = raised.value.args[0]
    assert isinstance(parsed, RedfishError)
    assert parsed.status_code == status_code
    assert parsed.code == "Base.1.18.GeneralError"
    assert parsed.message_extended == _EXTENDED_INFO


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [(200, "Ok"), (201, "Created"), (202, "AcceptedTaskGenerated"), (204, "Success")],
)
def test_base_default_error_handler_maps_success_codes(status_code, expected):
    """A 2xx code returns its mapped RedfishApiRespond — the line-689 tautology
    (status_code >= 200 or < 300, always true) that made this branch meaningless
    is fixed, so success codes resolve instead of falling through."""
    manager = _base_manager()
    result = manager.default_error_handler(_Response(status_code, {}))
    assert result.name == expected


def test_normalized_error_contract_round_trips_as_json_and_yaml(capsys):
    """JSON and YAML output preserve the same structured DMTF error payload."""
    parsed = RedfishManager.parse_error(_Response(501, _DMTF_ERROR_BODY))
    schema = _corpus_json(
        GB300_FULL_CORPUS,
        "172.25.230.37/_redfish_v1_JsonSchemas_redfish-error.json",
    )
    registry = _corpus_json(
        GB300_FULL_CORPUS,
        "172.25.230.37/_redfish_v1_Registries_Base.json",
    )
    normalized = {
        "status_code": parsed.status_code,
        "schema": {
            "@odata.id": schema["@odata.id"],
            "@odata.type": schema["@odata.type"],
            "Schema": schema["Schema"],
            "PublicationUri": schema["Location"][0]["PublicationUri"],
            "Uri": schema["Location"][0]["Uri"],
        },
        "registry": {
            "@odata.id": registry["@odata.id"],
            "Registry": registry["Registry"],
            "PublicationUri": registry["Location"][0]["PublicationUri"],
            "Uri": registry["Location"][0]["Uri"],
        },
        "error": {
            "code": parsed.code,
            "message": parsed.message,
            "@Message.ExtendedInfo": parsed.message_extended,
        },
    }

    json_printer(normalized, _args(yaml=False), colorized=False)
    json_out = capsys.readouterr().out
    assert json.loads(json_out) == normalized

    json_printer(normalized, _args(yaml=True), colorized=False)
    yaml_out = capsys.readouterr().out
    assert yaml.safe_load(yaml_out) == normalized
    assert "!!python" not in yaml_out
    assert "RedfishError" not in json_out
    assert "_message_extended" not in json_out


def _call_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _dict_string_keys(node):
    if not isinstance(node, ast.Dict):
        return set()
    return {
        key.value
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


def _uses_response_status_code(node):
    return any(
        isinstance(candidate, ast.Attribute) and candidate.attr == "status_code"
        for candidate in ast.walk(node)
    )


def _calls_redfish_error_parser(node):
    parser_names = {"parse_error", "_error_text", "default_error_handler"}
    return any(
        isinstance(candidate, ast.Call) and _call_name(candidate.func) in parser_names
        for candidate in ast.walk(node)
    )


def _is_handbuilt_error_command_result(node):
    if not isinstance(node, ast.Return):
        return False
    call = node.value
    if not isinstance(call, ast.Call):
        return False
    if _call_name(call.func) != "CommandResult":
        return False
    if not call.args:
        return False
    return {"error", "status_code"}.issubset(_dict_string_keys(call.args[0]))


def test_http_error_command_results_are_parser_backed():
    """Command HTTP errors must not bypass DMTF Redfish normalization."""
    offenders = []
    parser_modules = {
        "redfish_manager.py",
        "redfish_manager_base.py",
        "redfish_respond.py",
        "redfish_respond_error.py",
    }
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        if path.name in parser_modules:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _uses_response_status_code(node):
                continue
            if _calls_redfish_error_parser(node):
                continue
            for child in ast.walk(node):
                if _is_handbuilt_error_command_result(child):
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)}:{child.lineno} {node.name}"
                    )

    assert not offenders, (
        "HTTP error CommandResult payloads must be built from parse_error, "
        "_error_text, or default_error_handler so code/message/"
        "@Message.ExtendedInfo/schema data is not flattened:\n"
        + "\n".join(offenders)
    )
