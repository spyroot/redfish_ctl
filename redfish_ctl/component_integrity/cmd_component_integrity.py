"""Read Redfish ComponentIntegrity (SPDM attestation / Root-of-Trust).

    redfish_ctl component-integrity

Walks ``/redfish/v1/ComponentIntegrity`` -> each ComponentIntegrity leaf,
returning {Id, Type, Version, Enabled, TargetComponentURI, CertificateURI}.
Each leaf describes an SPDM attestation relationship for one Root-of-Trust
(ERoT/IRoT) protecting a component (BMC, CPU, GPU, FPGA, NIC/DPU); the
certificate link points at the device's measurement/identity cert chain.

Navigation is by link/``@odata.id`` with no hardcoded ids. By default the cert
chain is referenced by URI only — the PEM body is never emitted. Vendor-neutral:
ComponentIntegrity is a DMTF resource, so this works on any host exposing it.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult
from ..redfish_shared import RedfishApi


class QueryComponentIntegrity(IDracManager,
                              scm_type=ApiRequestType.ComponentIntegrity,
                              name='component-integrity',
                              metaclass=Singleton):
    """Read every ComponentIntegrity (SPDM/attestation) relationship."""

    def __init__(self, *args, **kwargs):
        """Initialize the component-integrity command."""
        super(QueryComponentIntegrity, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``component-integrity`` subcommand (read-only).

        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        help_text = "command read ComponentIntegrity (SPDM attestation) state"
        return cmd_parser, "component-integrity", help_text

    @staticmethod
    def _members(data):
        """Return the @odata.id strings from a Redfish collection, tolerantly.

        :param data: a Redfish collection body (dict carrying ``Members``).
        :return: list of member ``@odata.id`` strings (empty if none/malformed).
        """
        if not isinstance(data, dict):
            return []
        return [m["@odata.id"] for m in data.get("Members", [])
                if isinstance(m, dict) and isinstance(m.get("@odata.id"), str)]

    @staticmethod
    def _cert_uri(spdm):
        """Pull the responder certificate chain link out of the SPDM block.

        Layout: SPDM.IdentityAuthentication.ResponderAuthentication
        .ComponentCertificate.@odata.id. Any level may be absent.

        :param spdm: the leaf's ``SPDM`` sub-dict (or any non-dict).
        :return: the certificate ``@odata.id`` string, or None when absent.
        """
        if not isinstance(spdm, dict):
            return None
        node = spdm.get("IdentityAuthentication") or {}
        node = node.get("ResponderAuthentication") or {}
        cert = node.get("ComponentCertificate") or {}
        return cert.get("@odata.id") if isinstance(cert, dict) else None

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Walk the ComponentIntegrity collection and summarize each leaf.

        Tolerant of a host without the collection (returns an empty list) or a
        leaf missing the SPDM/cert sub-structure (CertificateURI is None).

        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: note async will subscribe to an event loop.
        :param do_expanded: issue an expanded ($expand) Redfish query.
        :return: CommandResult whose data is a list of per-leaf integrity rows
            (empty when the host exposes no ComponentIntegrity collection).
        """
        rows = []
        coll_uri = f"{RedfishApi.Version}/ComponentIntegrity"
        try:
            coll = self.base_query(coll_uri, do_async=do_async,
                                   do_expanded=do_expanded).data or {}
        except Exception:
            return CommandResult(rows, None, None, None)

        for leaf_uri in self._members(coll):
            try:
                ci = self.base_query(leaf_uri, do_async=do_async).data or {}
            except Exception:
                continue
            rows.append({
                "Id": ci.get("Id") or leaf_uri.rsplit("/", 1)[-1],
                "Type": ci.get("ComponentIntegrityType"),
                "Version": ci.get("ComponentIntegrityTypeVersion"),
                "Enabled": ci.get("ComponentIntegrityEnabled"),
                "TargetComponentURI": ci.get("TargetComponentURI"),
                "CertificateURI": self._cert_uri(ci.get("SPDM")),
            })
        return CommandResult(rows, None, None, None)
