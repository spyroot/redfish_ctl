"""Dual-mode tests for the CertificateService.GenerateCSR command."""

import pytest

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.redfish_manager_shared import ApiRequestType


def _post_requests(redfish_service):
    """Return POST requests recorded by the mock Redfish service.

    :param redfish_service: the MockRedfishService recording requests.
    :return: list of recorded POST requests.
    """
    return [request for request in redfish_service.requests if request.method == "POST"]


def test_cert_gen_csr_posts_payload_to_generic_certificate_service(
    redfish_mock_factory,
):
    """cert-gen-csr posts a reversible CSR payload to the discovered target."""
    manager, service = redfish_mock_factory("generic")

    result = manager.sync_invoke(
        ApiRequestType.CertificateGenerateCSR,
        "cert_gen_csr",
        common_name="bmc.example.test",
        organization="Example Org",
        organizational_unit="Platform",
        city="San Francisco",
        state="CA",
        country="US",
        key_pair_algorithm="TPM_ALG_RSA",
        key_bit_length=2048,
        key_curve_id="TPM_ECC_NIST_P256",
        key_usage=["DigitalSignature", "KeyEncipherment"],
        alternative_names=["bmc-alt.example.test,192.0.2.10"],
        certificate_collection="/redfish/v1/Systems/437XR1138R2/Certificates",
        contact_person="Ops Team",
        email="ops@example.test",
        given_name="Platform",
        initials="PT",
        surname="Team",
        unstructured_name="lab-bmc",
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["level"] == "reversible"
    assert result.data["target"] == (
        "/redfish/v1/CertificateService/Actions/CertificateService.GenerateCSR"
    )
    assert len(posts) == 1
    assert posts[0].path.lower() == (
        "/redfish/v1/certificateservice/actions/certificateservice.generatecsr"
    )
    assert posts[0].json() == {
        "CertificateCollection": {
            "@odata.id": "/redfish/v1/Systems/437XR1138R2/Certificates"
        },
        "City": "San Francisco",
        "CommonName": "bmc.example.test",
        "Country": "US",
        "AlternativeNames": ["bmc-alt.example.test", "192.0.2.10"],
        "ContactPerson": "Ops Team",
        "Email": "ops@example.test",
        "GivenName": "Platform",
        "Initials": "PT",
        "KeyBitLength": 2048,
        "KeyCurveId": "TPM_ECC_NIST_P256",
        "KeyPairAlgorithm": "TPM_ALG_RSA",
        "KeyUsage": ["DigitalSignature", "KeyEncipherment"],
        "Organization": "Example Org",
        "OrganizationalUnit": "Platform",
        "State": "CA",
        "Surname": "Team",
        "UnstructuredName": "lab-bmc",
    }


def test_cert_gen_csr_dry_run_suppresses_post(redfish_mock_factory):
    """cert-gen-csr --dry_run resolves the action target without POSTing."""
    manager, service = redfish_mock_factory("hpe")

    result = manager.sync_invoke(
        ApiRequestType.CertificateGenerateCSR,
        "cert_gen_csr",
        common_name="ilo.example.test",
        key_usage=["DigitalSignature"],
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["level"] == "reversible"
    assert result.data["target"] == (
        "/redfish/v1/CertificateService/Actions/CertificateService.GenerateCSR"
    )
    assert result.data["payload"] == {
        "CommonName": "ilo.example.test",
        "KeyUsage": ["DigitalSignature"],
    }
    assert _post_requests(service) == []


def test_cert_gen_csr_uses_supermicro_certificate_service_target(
    redfish_mock_factory,
):
    """cert-gen-csr discovers GenerateCSR on the Supermicro CertificateService."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.CertificateGenerateCSR,
        "cert_gen_csr",
        common_name="gb300.example.test",
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["target"] == (
        "/redfish/v1/CertificateService/Actions/CertificateService.GenerateCSR"
    )
    assert len(posts) == 1
    assert posts[0].json() == {"CommonName": "gb300.example.test"}


def test_cert_gen_csr_reports_missing_action_without_post(redfish_mock_factory):
    """A CertificateService without GenerateCSR reports the available actions."""
    manager, service = redfish_mock_factory("generic")
    service._overlay["/redfish/v1/certificateservice"] = {
        "@odata.id": "/redfish/v1/CertificateService",
        "Id": "CertificateService",
        "Actions": {},
    }

    result = manager.sync_invoke(
        ApiRequestType.CertificateGenerateCSR,
        "cert_gen_csr",
        common_name="bmc.example.test",
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "action '#CertificateService.GenerateCSR' not found on "
        "/redfish/v1/CertificateService"
    )
    assert result.data == {
        "action": "#CertificateService.GenerateCSR",
        "available": [],
    }
    assert _post_requests(service) == []


def test_cert_gen_csr_rejects_invalid_hpe_key_usage_without_post(
    redfish_mock_factory,
):
    """HPE inline KeyUsage allowable values reject invalid payloads before POST."""
    manager, service = redfish_mock_factory("hpe")

    result = manager.sync_invoke(
        ApiRequestType.CertificateGenerateCSR,
        "cert_gen_csr",
        common_name="ilo.example.test",
        key_usage=["NotAKeyUsage"],
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "invalid value for CertificateService.GenerateCSR KeyUsage: "
        "NotAKeyUsage; allowed: CRLSigning, ClientAuthentication, CodeSigning, "
        "DataEncipherment, DecipherOnly, DigitalSignature, EmailProtection, "
        "EncipherOnly, KeyAgreement, KeyCertSign, KeyEncipherment, "
        "NonRepudiation, OCSPSigning, ServerAuthentication, Timestamping"
    )
    assert result.data["validation_errors"] == [
        {
            "parameter": "KeyUsage",
            "value": "NotAKeyUsage",
            "allowed": [
                "CRLSigning",
                "ClientAuthentication",
                "CodeSigning",
                "DataEncipherment",
                "DecipherOnly",
                "DigitalSignature",
                "EmailProtection",
                "EncipherOnly",
                "KeyAgreement",
                "KeyCertSign",
                "KeyEncipherment",
                "NonRepudiation",
                "OCSPSigning",
                "ServerAuthentication",
                "Timestamping",
            ],
        }
    ]
    assert _post_requests(service) == []


def test_cert_gen_csr_missing_certificate_collection_stops_before_post(
    redfish_mock_factory,
):
    """A missing CertificateCollection URI returns an error without POSTing."""
    manager, service = redfish_mock_factory("generic")

    result = manager.sync_invoke(
        ApiRequestType.CertificateGenerateCSR,
        "cert_gen_csr",
        common_name="bmc.example.test",
        certificate_collection="/redfish/v1/Systems/437XR1138R2/Certificates/Missing",
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "certificate collection not found: "
        "/redfish/v1/Systems/437XR1138R2/Certificates/Missing"
    )
    assert result.data == {
        "CertificateCollection": {
            "@odata.id": "/redfish/v1/Systems/437XR1138R2/Certificates/Missing"
        }
    }
    assert _post_requests(service) == []


def test_cert_gen_csr_rejects_certificate_member_uri_without_post(
    redfish_mock_factory,
):
    """A Certificate member URI is not accepted as a collection target."""
    manager, service = redfish_mock_factory("generic")

    result = manager.sync_invoke(
        ApiRequestType.CertificateGenerateCSR,
        "cert_gen_csr",
        common_name="bmc.example.test",
        certificate_collection=(
            "/redfish/v1/Systems/437XR1138R2/Certificates/contoso-root"
        ),
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "certificate collection URI is not a CertificateCollection: "
        "/redfish/v1/Systems/437XR1138R2/Certificates/contoso-root"
    )
    assert result.data == {
        "CertificateCollection": {
            "@odata.id": (
                "/redfish/v1/Systems/437XR1138R2/Certificates/contoso-root"
            )
        },
        "resource_type": "#Certificate.v1_8_1.Certificate",
    }
    assert _post_requests(service) == []


def test_cert_gen_csr_rejects_relative_certificate_collection_uri(
    redfish_mock_factory,
):
    """CertificateCollection links must be absolute Redfish resource URIs."""
    manager, service = redfish_mock_factory("generic")

    with pytest.raises(InvalidArgument, match="absolute Redfish URI"):
        manager.sync_invoke(
            ApiRequestType.CertificateGenerateCSR,
            "cert_gen_csr",
            common_name="bmc.example.test",
            certificate_collection="Systems/437XR1138R2/Certificates",
        )

    assert _post_requests(service) == []
