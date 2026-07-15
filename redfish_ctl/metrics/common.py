"""Helpers for Redfish metrics link readers."""


def members(data):
    """Extract member ``@odata.id`` URIs from a Redfish collection.

    :param data: parsed Redfish collection resource, or any value.
    :return: list of member ``@odata.id`` URIs; empty when ``data`` is not a dict.
    """
    if not isinstance(data, dict):
        return []
    return [
        member["@odata.id"]
        for member in data.get("Members", [])
        if isinstance(member, dict)
        and isinstance(member.get("@odata.id"), str)
    ]


def link(data, key):
    """Return the ``@odata.id`` of a single Redfish link field.

    :param data: parsed Redfish resource, or any value.
    :param key: name of the link property to read.
    :return: the linked ``@odata.id`` string, or ``None`` when absent.
    """
    value = data.get(key) if isinstance(data, dict) else None
    if isinstance(value, dict) and isinstance(value.get("@odata.id"), str):
        return value["@odata.id"]
    return None


def resource_id(uri):
    """Return the trailing path segment (resource id) of a Redfish URI.

    :param uri: Redfish resource URI.
    :return: the last path segment after stripping any trailing slash.
    """
    return uri.rstrip("/").rsplit("/", 1)[-1]


def nvidia_oem(data):
    """Return the ``Oem.Nvidia`` block of a Redfish resource.

    :param data: parsed Redfish resource, or any value.
    :return: the ``Oem.Nvidia`` dict, or ``None`` when absent.
    """
    oem = data.get("Oem") if isinstance(data, dict) else None
    if not isinstance(oem, dict):
        return None
    nvidia = oem.get("Nvidia")
    return nvidia if isinstance(nvidia, dict) else None
