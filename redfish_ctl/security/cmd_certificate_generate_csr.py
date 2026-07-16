"""Generate a certificate signing request through CertificateService.GenerateCSR.

    redfish_ctl cert-gen-csr --common-name bmc.example.test --dry_run
    redfish_ctl cert-gen-csr --common-name bmc.example.test --organization Example

The command resolves ``#CertificateService.GenerateCSR`` from the standard
CertificateService resource and posts only the CSR subject/key fields provided by
the operator. GenerateCSR is reversible in the action policy: it creates a CSR
and may retain a private key for the eventual signed certificate, but it does not
replace the active certificate.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton
from ..redfish_shared import RedfishApi

_GENERATE_CSR_ACTION = "#CertificateService.GenerateCSR"


class CertificateGenerateCSR(RedfishManagerBase,
                             scm_type=ApiRequestType.CertificateGenerateCSR,
                             name="cert_gen_csr",
                             metaclass=Singleton):
    """Generate a CSR through the Redfish CertificateService."""

    def __init__(self, *args, **kwargs):
        """Initialize the cert-gen-csr command."""
        super(CertificateGenerateCSR, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``cert-gen-csr`` subcommand.

        :param cls: command class supplying the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--common-name",
            required=True,
            dest="common_name",
            type=str,
            help="CSR CommonName, typically the BMC DNS name",
        )
        cmd_parser.add_argument(
            "--organization",
            required=False,
            dest="organization",
            type=str,
            default=None,
            help="CSR Organization field",
        )
        cmd_parser.add_argument(
            "--organizational-unit",
            required=False,
            dest="organizational_unit",
            type=str,
            default=None,
            help="CSR OrganizationalUnit field",
        )
        cmd_parser.add_argument(
            "--city",
            required=False,
            dest="city",
            type=str,
            default=None,
            help="CSR City/locality field",
        )
        cmd_parser.add_argument(
            "--state",
            required=False,
            dest="state",
            type=str,
            default=None,
            help="CSR State or province field",
        )
        cmd_parser.add_argument(
            "--country",
            required=False,
            dest="country",
            type=str,
            default=None,
            help="CSR two-letter Country field",
        )
        cmd_parser.add_argument(
            "--contact-person",
            required=False,
            dest="contact_person",
            type=str,
            default=None,
            help="CSR ContactPerson field",
        )
        cmd_parser.add_argument(
            "--email",
            required=False,
            dest="email",
            type=str,
            default=None,
            help="CSR Email field",
        )
        cmd_parser.add_argument(
            "--given-name",
            required=False,
            dest="given_name",
            type=str,
            default=None,
            help="CSR GivenName field",
        )
        cmd_parser.add_argument(
            "--initials",
            required=False,
            dest="initials",
            type=str,
            default=None,
            help="CSR Initials field",
        )
        cmd_parser.add_argument(
            "--surname",
            required=False,
            dest="surname",
            type=str,
            default=None,
            help="CSR Surname field",
        )
        cmd_parser.add_argument(
            "--unstructured-name",
            required=False,
            dest="unstructured_name",
            type=str,
            default=None,
            help="CSR UnstructuredName field",
        )
        cmd_parser.add_argument(
            "--key-pair-algorithm",
            required=False,
            dest="key_pair_algorithm",
            type=str,
            default=None,
            help="TPM algorithm name, such as TPM_ALG_RSA",
        )
        cmd_parser.add_argument(
            "--key-bit-length",
            required=False,
            dest="key_bit_length",
            type=int,
            default=None,
            help="key size in bits when required by the key algorithm",
        )
        cmd_parser.add_argument(
            "--key-curve-id",
            required=False,
            dest="key_curve_id",
            type=str,
            default=None,
            help="TPM ECC curve id when required by the key algorithm",
        )
        cmd_parser.add_argument(
            "--key-usage",
            required=False,
            dest="key_usage",
            action="append",
            default=None,
            help="certificate KeyUsage value; repeat or comma-separate values",
        )
        cmd_parser.add_argument(
            "--alternative-name",
            required=False,
            dest="alternative_names",
            action="append",
            default=None,
            help="subjectAltName DNS/IP value; repeat or comma-separate values",
        )
        cmd_parser.add_argument(
            "--certificate-collection",
            required=False,
            dest="certificate_collection",
            type=str,
            default=None,
            help="certificate collection URI where the signed certificate installs",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="resolve the GenerateCSR target and show the payload without POSTing",
        )
        return cmd_parser, "cert-gen-csr", "command generate a CertificateService CSR"

    @staticmethod
    def _string_list(values):
        """Normalize repeated and comma-separated CLI values into a list.

        :param values: None, a string, or a list/tuple of strings.
        :return: flattened list with empty values removed, or None when empty.
        """
        if values is None:
            return None
        raw_values = values if isinstance(values, (list, tuple)) else [values]
        items = []
        for value in raw_values:
            if value is None:
                continue
            items.extend(part.strip() for part in str(value).split(","))
        return [item for item in items if item] or None

    @staticmethod
    def _certificate_collection_link(uri):
        """Build a Redfish link object from a certificate collection URI.

        :param uri: Redfish certificate collection URI, or None.
        :return: ``{"@odata.id": uri}`` when set, otherwise None.
        """
        if not uri:
            return None
        return {"@odata.id": uri}

    def _certificate_service_uri(self, do_async):
        """Resolve the CertificateService URI from the service root, with a standard fallback.

        :param do_async: issue the service-root query over the async Redfish path when True.
        :return: the CertificateService ``@odata.id``, or the standard fallback URI.
        """
        try:
            root = self.base_query(RedfishApi.Version, do_async=do_async).data or {}
        except Exception:
            root = {}
        link = root.get("CertificateService")
        if isinstance(link, dict) and link.get("@odata.id"):
            return link["@odata.id"]
        return f"{RedfishApi.Version}/CertificateService"

    def _payload(self,
                 common_name,
                 organization=None,
                 organizational_unit=None,
                 city=None,
                 state=None,
                 country=None,
                 contact_person=None,
                 email=None,
                 given_name=None,
                 initials=None,
                 surname=None,
                 unstructured_name=None,
                 key_pair_algorithm=None,
                 key_bit_length=None,
                 key_curve_id=None,
                 key_usage=None,
                 alternative_names=None,
                 certificate_collection=None):
        """Build a GenerateCSR payload from the provided non-empty fields.

        :param common_name: CSR CommonName value.
        :param organization: CSR Organization value.
        :param organizational_unit: CSR OrganizationalUnit value.
        :param city: CSR City/locality value.
        :param state: CSR State/province value.
        :param country: CSR two-letter Country value.
        :param contact_person: CSR ContactPerson value.
        :param email: CSR Email value.
        :param given_name: CSR GivenName value.
        :param initials: CSR Initials value.
        :param surname: CSR Surname value.
        :param unstructured_name: CSR UnstructuredName value.
        :param key_pair_algorithm: optional TPM key-pair algorithm name.
        :param key_bit_length: optional key length in bits.
        :param key_curve_id: optional TPM ECC curve id.
        :param key_usage: optional KeyUsage values.
        :param alternative_names: optional subject alternative names.
        :param certificate_collection: optional target certificate collection URI.
        :return: GenerateCSR payload containing only supplied fields.
        """
        payload = {
            "CommonName": common_name,
            "Organization": organization,
            "OrganizationalUnit": organizational_unit,
            "City": city,
            "State": state,
            "Country": country,
            "ContactPerson": contact_person,
            "Email": email,
            "GivenName": given_name,
            "Initials": initials,
            "Surname": surname,
            "UnstructuredName": unstructured_name,
            "KeyPairAlgorithm": key_pair_algorithm,
            "KeyBitLength": key_bit_length,
            "KeyCurveId": key_curve_id,
            "KeyUsage": self._string_list(key_usage),
            "AlternativeNames": self._string_list(alternative_names),
            "CertificateCollection": self._certificate_collection_link(
                certificate_collection
            ),
        }
        return {key: value for key, value in payload.items() if value is not None}

    def execute(self,
                common_name: Optional[str] = None,
                organization: Optional[str] = None,
                organizational_unit: Optional[str] = None,
                city: Optional[str] = None,
                state: Optional[str] = None,
                country: Optional[str] = None,
                contact_person: Optional[str] = None,
                email: Optional[str] = None,
                given_name: Optional[str] = None,
                initials: Optional[str] = None,
                surname: Optional[str] = None,
                unstructured_name: Optional[str] = None,
                key_pair_algorithm: Optional[str] = None,
                key_bit_length: Optional[int] = None,
                key_curve_id: Optional[str] = None,
                key_usage=None,
                alternative_names=None,
                certificate_collection: Optional[str] = None,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Resolve and invoke ``CertificateService.GenerateCSR``.

        :param common_name: CSR CommonName value.
        :param organization: CSR Organization value.
        :param organizational_unit: CSR OrganizationalUnit value.
        :param city: CSR City/locality value.
        :param state: CSR State/province value.
        :param country: CSR two-letter Country value.
        :param contact_person: CSR ContactPerson value.
        :param email: CSR Email value.
        :param given_name: CSR GivenName value.
        :param initials: CSR Initials value.
        :param surname: CSR Surname value.
        :param unstructured_name: CSR UnstructuredName value.
        :param key_pair_algorithm: optional TPM key-pair algorithm name.
        :param key_bit_length: optional key length in bits.
        :param key_curve_id: optional TPM ECC curve id.
        :param key_usage: optional KeyUsage values.
        :param alternative_names: optional subject alternative names.
        :param certificate_collection: optional target certificate collection URI.
        :param dry_run: resolve the target and show the payload without POSTing.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue the underlying query and POST on the async path.
        :return: a CommandResult with the GenerateCSR result or dry-run preview.
        """
        return self.invoke_action(
            self._certificate_service_uri(do_async),
            "GenerateCSR",
            payload=self._payload(
                common_name,
                organization=organization,
                organizational_unit=organizational_unit,
                city=city,
                state=state,
                country=country,
                contact_person=contact_person,
                email=email,
                given_name=given_name,
                initials=initials,
                surname=surname,
                unstructured_name=unstructured_name,
                key_pair_algorithm=key_pair_algorithm,
                key_bit_length=key_bit_length,
                key_curve_id=key_curve_id,
                key_usage=key_usage,
                alternative_names=alternative_names,
                certificate_collection=certificate_collection,
            ),
            full_action_type=_GENERATE_CSR_ACTION,
            do_async=do_async,
            dry_run=bool(dry_run),
        )
