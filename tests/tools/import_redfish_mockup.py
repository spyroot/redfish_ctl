"""Import a DMTF/HPE Redfish mockup (index.json dir-tree) into flat test fixtures.

DMTF/HPE mockups (e.g. HewlettPackard/ilo-redfish-emulator, DMTF Redfish-Mockup-
Creator output) store each resource as ``<path>/index.json`` under a root that
maps to ``/redfish/v1``. Our mock (tests/conftest.py) serves flat files named by
URL: ``/redfish/v1/Systems/1`` -> ``_redfish_v1_Systems_1.json``. This converts
between the two.

By default it does a BOUNDED, link-consistent walk from the service root (follow
collection Members, capped, plus a whitelist of resource links) so a small,
representative, self-consistent slice can be vendored for offline vendor tests.
Pass --full to convert the entire tree.

Usage:
    python tests/tools/import_redfish_mockup.py --mockup <dir> --out tests/hpe_fixtures
"""
import argparse
import json
import os
from collections import deque

# resource links worth following for our command surface (single {@odata.id} links)
LINK_KEYS = {
    "Systems", "Managers", "Chassis", "Sensors", "Thermal", "Power",
    "PowerSubsystem", "ThermalSubsystem", "NetworkAdapters", "Processors",
    "EthernetInterfaces", "NetworkInterfaces", "VirtualMedia", "EventService",
    "CertificateService", "ComponentIntegrity", "TelemetryService",
    "MetricReports", "MetricReportDefinitions", "Ports", "Metrics", "Memory",
    "LogServices", "Entries", "PCIeDevices", "PCIeFunctions",
    "SecureBoot", "SecureBootDatabases", "Certificates", "Signatures",
    "Storage", "Volumes", "Drives",
    "NetworkDeviceFunctions",
    "BootOptions", "Boot",
    "UpdateService", "FirmwareInventory", "SoftwareInventory", "Triggers",
}
ROOT = "/redfish/v1"


def url_to_flat(url):
    """/redfish/v1/Systems/1 -> _redfish_v1_Systems_1.json (matches conftest)."""
    return "_" + url.strip("/").replace("/", "_") + ".json"


def load_node(mockup_root, url):
    """Load the index.json for a Redfish URL from the mockup dir tree."""
    rel = url[len(ROOT):].strip("/")  # "" for the service root
    path = os.path.join(mockup_root, rel, "index.json") if rel else os.path.join(mockup_root, "index.json")
    if not os.path.isfile(path):
        return None
    with open(path) as fh:
        return json.load(fh)


def children(data, max_members):
    """URLs to visit next: capped collection Members + whitelisted resource links."""
    out = []
    if not isinstance(data, dict):
        return out
    members = data.get("Members")
    if isinstance(members, list):
        for m in members[:max_members]:
            if isinstance(m, dict) and isinstance(m.get("@odata.id"), str):
                out.append(m["@odata.id"])
    for key in LINK_KEYS:
        link = data.get(key)
        if isinstance(link, dict) and isinstance(link.get("@odata.id"), str):
            out.append(link["@odata.id"])
    return out


def convert(mockup_root, out_dir, full, max_members, max_files):
    os.makedirs(out_dir, exist_ok=True)
    seen, written = set(), 0
    queue = deque([ROOT])
    while queue:
        url = queue.popleft()
        if not url.startswith(ROOT) or url in seen:
            continue
        seen.add(url)
        data = load_node(mockup_root, url)
        if data is None:
            continue
        with open(os.path.join(out_dir, url_to_flat(url)), "w") as fh:
            json.dump(data, fh, indent=2)
        written += 1
        if not full and written >= max_files:
            break
        cap = 10 ** 9 if full else max_members
        for child in children(data, cap):
            if child not in seen:
                queue.append(child)
    return written


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mockup", required=True, help="mockup root dir (maps to /redfish/v1)")
    ap.add_argument("--out", required=True, help="output fixtures dir")
    ap.add_argument("--full", action="store_true", help="convert the whole tree (no bounds)")
    ap.add_argument("--max-members", type=int, default=4, help="members kept per collection (bounded mode)")
    ap.add_argument("--max-files", type=int, default=200, help="max files (bounded mode)")
    args = ap.parse_args()
    n = convert(args.mockup, args.out, args.full, args.max_members, args.max_files)
    print(f"wrote {n} fixtures to {args.out}")


if __name__ == "__main__":
    main()
