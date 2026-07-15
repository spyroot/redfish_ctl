"""Read Redfish certificate inventory without returning certificate bodies.

    redfish_ctl certificates

Walks CertificateService and its CertificateLocations, plus each system and
manager Certificates collection, returning collection and per-certificate
metadata rows (issuer, subject, validity, key usage) but never the certificate
body itself.
"""

from abc import abstractmethod
from typing import Optional

from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult
from ..redfish_shared import RedfishApi


class Certificates(RedfishManagerBase,
                   scm_type=ApiRequestType.CertificatesQuery,
                   name="certificates",
                   metaclass=Singleton):
    """Read CertificateService and linked certificate collection metadata."""

    def __init__(self, *args, **kwargs):
        """Initialize the certificates command."""
        super(Certificates, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the read-only certificates subcommand.

        :param cls: command class supplying the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        return cmd_parser, "certificates", "command read Redfish certificate inventory"

    @staticmethod
    def _members(data):
        """Return the ``@odata.id`` strings from a Redfish collection, tolerantly.

        :param data: Redfish collection body (or any value).
        :return: list of member ``@odata.id`` strings; empty list if not a collection.
        """
        if not isinstance(data, dict):
            return []
        return [
            member["@odata.id"]
            for member in data.get("Members", [])
            if isinstance(member, dict)
            and isinstance(member.get("@odata.id"), str)
        ]

    @staticmethod
    def _link(data, key):
        """Return the ``@odata.id`` of a single ``{key: {@odata.id}}`` link, or None.

        :param data: resource body holding the link (or any value).
        :param key: name of the link property to read.
        :return: the linked ``@odata.id`` string, or None when absent.
        """
        link = (data or {}).get(key)
        return link.get("@odata.id") if isinstance(link, dict) else None

    @staticmethod
    def _links(data, key):
        """Return the ``@odata.id`` strings for a single link or a list of links.

        :param data: resource body holding the link property (or any value).
        :param key: name of the link property to read.
        :return: list of linked ``@odata.id`` strings; empty list when absent.
        """
        value = (data or {}).get(key)
        if isinstance(value, dict):
            uri = value.get("@odata.id")
            return [uri] if isinstance(uri, str) else []
        if isinstance(value, list):
            return [
                item["@odata.id"]
                for item in value
                if isinstance(item, dict)
                and isinstance(item.get("@odata.id"), str)
            ]
        return []

    @staticmethod
    def _key_usage(data):
        """Return the certificate ``KeyUsage`` values as a list, tolerantly.

        :param data: certificate body (or any value).
        :return: the KeyUsage values as a list; empty list when absent.
        """
        usage = (data or {}).get("KeyUsage")
        if isinstance(usage, list):
            return usage
        if isinstance(usage, str):
            return [usage]
        return []

    @staticmethod
    def _resource_id(uri):
        """Return the trailing id segment of a Redfish resource URI.

        :param uri: Redfish resource path.
        :return: the last path segment of the URI.
        """
        return uri.rstrip("/").rsplit("/", 1)[-1]

    def _get(self, uri, do_async):
        """GET a resource body, returning ``{}`` on empty URI or any failure.

        :param uri: Redfish resource path to fetch; a falsy URI skips the query.
        :param do_async: when set, the query subscribes to an event loop for async execution.
        :return: the resource body as a dict, or ``{}`` when empty or on failure.
        """
        if not uri:
            return {}
        try:
            data = self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _discover(self, discover):
        """Call a discovery callable, returning ``[]`` on any failure.

        :param discover: zero-argument callable that returns resource URIs.
        :return: the discovered URIs, or an empty list on failure.
        """
        try:
            return discover() or []
        except Exception:
            return []

    def _certificate_locations(self, locations):
        """Walk a CertificateLocations body for certificate collection URIs.

        :param locations: CertificateLocations body to walk.
        :return: list of URIs ending in ``/Certificates`` found in the tree.
        """
        found = []

        def visit(value):
            """Recursively collect ``/Certificates`` URIs into ``found``.

            :param value: a node (dict, list, or scalar) from the locations tree.
            """
            if isinstance(value, dict):
                uri = value.get("@odata.id")
                if isinstance(uri, str) and uri.rstrip("/").endswith("/Certificates"):
                    found.append(uri)
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(locations)
        return found

    def _certificate_row(self, cert, cert_uri, collection_uri):
        """Build a metadata row for a certificate (never its body).

        :param cert: certificate resource body.
        :param cert_uri: URI of the certificate resource.
        :param collection_uri: URI of the collection the certificate belongs to.
        :return: a metadata row dict (id, issuer, subject, validity, key usage, links).
        """
        links = cert.get("Links") if isinstance(cert.get("Links"), dict) else {}
        return {
            "Id": cert.get("Id") or self._resource_id(cert_uri),
            "Name": cert.get("Name"),
            "CertificateType": cert.get("CertificateType"),
            "Issuer": cert.get("Issuer"),
            "Subject": cert.get("Subject"),
            "ValidNotBefore": cert.get("ValidNotBefore"),
            "ValidNotAfter": cert.get("ValidNotAfter"),
            "KeyUsage": self._key_usage(cert),
            "Uri": cert.get("@odata.id", cert_uri),
            "CollectionUri": collection_uri,
            "IssuerUri": self._link(links, "Issuer"),
            "SubjectUris": self._links(links, "Subjects"),
        }

    @staticmethod
    def _summary(certificate_service, collections, certificates):
        """Build a summary of service presence and collection/certificate counts.

        :param certificate_service: the certificate service row, or None when absent.
        :param collections: the collected certificate collection rows.
        :param certificates: the collected certificate metadata rows.
        :return: a summary dict with service presence and counts.
        """
        return {
            "certificate_service": certificate_service is not None,
            "collections": len(collections),
            "certificates": len(certificates),
        }

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Read certificate service and linked certificate collections.

        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: when set, each Redfish query subscribes to an event loop for async execution.
        :param do_expanded: accepted for CLI compatibility; not used by this command.
        :return: CommandResult wrapping the summary, certificate service, collections, and certificate rows.
        """
        certificate_service = None
        certificate_collections = []
        certificates = []
        collection_links = []
        seen_collections = set()

        def add_collection(uri, source):
            """Record a not-yet-seen certificate collection URI with its source label.

            :param uri: certificate collection URI to enqueue.
            :param source: human-readable origin label for the collection.
            """
            if uri and uri not in seen_collections:
                seen_collections.add(uri)
                collection_links.append((uri, source))

        service = self._get(f"{RedfishApi.Version}/CertificateService", do_async)
        if service:
            locations_uri = self._link(service, "CertificateLocations")
            certificate_service = {
                "Id": service.get("Id"),
                "Name": service.get("Name"),
                "Uri": service.get("@odata.id", f"{RedfishApi.Version}/CertificateService"),
                "CertificateLocationsUri": locations_uri,
            }
            for uri in self._certificate_locations(self._get(locations_uri, do_async)):
                add_collection(uri, "CertificateService")

        for system_uri in self._discover(self.discover_computer_system_ids):
            system = self._get(system_uri, do_async)
            add_collection(
                self._link(system, "Certificates"),
                f"System {system.get('Id') or self._resource_id(system_uri)}",
            )

        for manager_uri in self._discover(self.discover_manager_ids):
            manager = self._get(manager_uri, do_async)
            add_collection(
                self._link(manager, "Certificates"),
                f"Manager {manager.get('Id') or self._resource_id(manager_uri)}",
            )

        for collection_uri, source in collection_links:
            collection = self._get(collection_uri, do_async)
            if not collection:
                continue
            members = self._members(collection)
            certificate_collections.append({
                "Source": source,
                "Uri": collection.get("@odata.id", collection_uri),
                "Name": collection.get("Name"),
                "MemberCount": collection.get("Members@odata.count", len(members)),
            })
            for cert_uri in members:
                cert = self._get(cert_uri, do_async)
                if cert:
                    certificates.append(
                        self._certificate_row(cert, cert_uri, collection_uri)
                    )

        data = {
            "summary": self._summary(
                certificate_service,
                certificate_collections,
                certificates,
            ),
            "certificate_service": certificate_service,
            "collections": certificate_collections,
            "certificates": certificates,
        }
        return CommandResult(data, None, None, None)
