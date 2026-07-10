"""Helpers for Redfish metrics link readers."""


def members(data):
    if not isinstance(data, dict):
        return []
    return [
        member["@odata.id"]
        for member in data.get("Members", [])
        if isinstance(member, dict)
        and isinstance(member.get("@odata.id"), str)
    ]


def link(data, key):
    value = data.get(key) if isinstance(data, dict) else None
    if isinstance(value, dict) and isinstance(value.get("@odata.id"), str):
        return value["@odata.id"]
    return None


def resource_id(uri):
    return uri.rstrip("/").rsplit("/", 1)[-1]


def nvidia_oem(data):
    oem = data.get("Oem") if isinstance(data, dict) else None
    if not isinstance(oem, dict):
        return None
    nvidia = oem.get("Nvidia")
    return nvidia if isinstance(nvidia, dict) else None
