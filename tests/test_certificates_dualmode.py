"""Dual-mode tests for the read-only certificates command."""
import json

import pytest

from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_manager_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def test_certificates_reads_generic_inventory_without_secret_material(
    redfish_mock_factory,
):
    """certificates returns safe metadata from generic certificate fixtures."""
    manager, service = redfish_mock_factory("generic")

    result = manager.sync_invoke(ApiRequestType.CertificatesQuery, "certificates")

    assert isinstance(result, CommandResult)
    assert result.discovered is None
    assert result.extra is None
    assert result.error is None
    json.dumps(result.data, sort_keys=True)

    assert result.data["summary"] == {
        "certificate_service": True,
        "collections": 1,
        "certificates": 2,
    }
    assert result.data["certificate_service"] == {
        "Id": "CertificateService",
        "Name": "Certificate Service",
        "Uri": "/redfish/v1/CertificateService",
        "CertificateLocationsUri": (
            "/redfish/v1/CertificateService/CertificateLocations"
        ),
    }
    assert result.data["collections"] == [
        {
            "Source": "System 437XR1138R2",
            "Uri": "/redfish/v1/Systems/437XR1138R2/Certificates",
            "Name": "Certificates Collection",
            "MemberCount": 3,
        }
    ]

    certificates = {row["Id"]: row for row in result.data["certificates"]}
    assert certificates["contoso-root"] == {
        "Id": "contoso-root",
        "Name": "Contoso Root CA",
        "CertificateType": "PEM",
        "Issuer": None,
        "Subject": None,
        "ValidNotBefore": None,
        "ValidNotAfter": None,
        "KeyUsage": [],
        "Uri": "/redfish/v1/Systems/437XR1138R2/Certificates/contoso-root",
        "CollectionUri": "/redfish/v1/Systems/437XR1138R2/Certificates",
        "IssuerUri": None,
        "SubjectUris": [
            "/redfish/v1/Systems/437XR1138R2/Certificates/contoso-subca"
        ],
    }
    assert certificates["contoso-subca"]["IssuerUri"] == (
        "/redfish/v1/Systems/437XR1138R2/Certificates/contoso-root"
    )
    assert all("CertificateString" not in row for row in result.data["certificates"])
    assert all(
        request.method not in {"POST", "PATCH", "DELETE"}
        for request in service.requests
    )


def test_certificates_tolerates_root_without_certificate_links():
    """certificates returns an empty inventory when no certificate links exist."""
    requests_mock = pytest.importorskip("requests_mock")
    requests = []

    def get_cb(request, context):
        requests.append(request)
        payloads = {
            "/redfish/v1/Systems": {
                "Members": [],
                "@odata.id": "/redfish/v1/Systems",
            },
            "/redfish/v1/Managers": {
                "Members": [],
                "@odata.id": "/redfish/v1/Managers",
            },
        }
        payload = payloads.get(request.path)
        if payload is None:
            context.status_code = 404
            return json.dumps({"error": f"no fixture for {request.path}"})
        context.status_code = 200
        return json.dumps(payload)

    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=get_cb)
        mocker.patch(requests_mock.ANY, text=get_cb)
        mocker.post(requests_mock.ANY, text=get_cb)
        mocker.delete(requests_mock.ANY, text=get_cb)
        manager = RedfishManagerBase(
            idrac_ip="mock-no-certs",
            idrac_username="root",
            idrac_password="mock",
            insecure=True,
            is_debug=False,
        )

        result = manager.sync_invoke(
            ApiRequestType.CertificatesQuery,
            "certificates",
        )

    assert isinstance(result, CommandResult)
    assert result.data == {
        "summary": {
            "certificate_service": False,
            "collections": 0,
            "certificates": 0,
        },
        "certificate_service": None,
        "collections": [],
        "certificates": [],
    }
    assert all(
        request.method not in {"POST", "PATCH", "DELETE"}
        for request in requests
    )
