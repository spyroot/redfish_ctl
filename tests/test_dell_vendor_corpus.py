"""Contracts for the Dell iDRAC (PowerEdge XR8620t) vendor corpus + mutation rules.

The corpus under tests/dell_xr8620t_corpus is a real iDRAC Redfish capture; the
mock serves it unchanged and the dell_xr8620t.yaml mutation rules drive its
write classes. Dell uses the iDRAC path scheme (System.Embedded.1 /
iDRAC.Embedded.1), so these rules are distinct from the GB300 set.

Author Mus <spyroot@gmail.com>
"""
from __future__ import annotations

import importlib.util
import json
import urllib.error
import urllib.request
from pathlib import Path

import yaml
from vendor_corpus import corpus_dir

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_MODULE = REPO_ROOT / "k8s" / "sandbox" / "mock_bmc_server.py"
DELL_CORPUS = corpus_dir(REPO_ROOT / "tests" / "dell_xr8620t_corpus.tar.gz", "10.252.252.209")
DELL_RULES = REPO_ROOT / "tests" / "mutation_rules" / "dell_xr8620t.yaml"

SYSTEM = "/redfish/v1/Systems/System.Embedded.1"
RESET = f"{SYSTEM}/Actions/ComputerSystem.Reset"
BIOS = f"{SYSTEM}/Bios"
BIOS_SETTINGS = f"{BIOS}/Settings"
VOLUMES = f"{SYSTEM}/Storage/PCIeSSD.Integrated.1-C/Volumes"
SEL_CLEAR = "/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel/Actions/LogService.ClearLog"


def _load_server_module():
    spec = importlib.util.spec_from_file_location("mock_bmc_server", SERVER_MODULE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _http(base: str, path: str, method: str = "GET", body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    request = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            raw = response.read().decode("utf-8")
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        return exc.code, (json.loads(raw) if raw else None)


def test_dell_corpus_serves_a_real_idrac() -> None:
    """The committed corpus serves as a Dell iDRAC (ServiceRoot + System member)."""
    module = _load_server_module()
    with module.run_server("127.0.0.1", 0, DELL_CORPUS) as srv:
        base = "http://{}:{}".format(*srv.server_address)
        _, root = _http(base, "/redfish/v1")
        assert root["Vendor"] == "Dell"
        _, system = _http(base, SYSTEM)
        assert system["Manufacturer"] == "Dell Inc."
        assert system["Model"] == "PowerEdge XR8620t"
        assert system["PowerState"] == "On"


def test_dell_power_boot_and_bios_mutations() -> None:
    """Reset, one-time boot revert, and staged-BIOS apply all reconcile on the corpus."""
    module = _load_server_module()
    with module.run_server("127.0.0.1", 0, DELL_CORPUS, mutation_rules=DELL_RULES) as srv:
        base = "http://{}:{}".format(*srv.server_address)

        # power cycle
        assert _http(base, RESET, "POST", {"ResetType": "ForceOff"})[0] == 204
        assert _http(base, SYSTEM)[1]["PowerState"] == "Off"
        assert _http(base, RESET, "POST", {"ResetType": "On"})[0] == 204
        assert _http(base, SYSTEM)[1]["PowerState"] == "On"

        # arm a one-time PXE boot, then a reset consumes it
        assert _http(base, SYSTEM, "PATCH",
                     {"Boot": {"BootSourceOverrideTarget": "Pxe",
                               "BootSourceOverrideEnabled": "Once"}})[0] == 200
        assert _http(base, SYSTEM)[1]["Boot"]["BootSourceOverrideEnabled"] == "Once"
        assert _http(base, RESET, "POST", {"ResetType": "GracefulRestart"})[0] == 204
        assert _http(base, SYSTEM)[1]["Boot"]["BootSourceOverrideEnabled"] == "Disabled"

        # stage a BIOS attribute; it applies to the live resource only after a reset
        assert _http(base, SYSTEM)[1]  # sanity
        assert _http(base, BIOS)[1]["Attributes"]["AEPErrorInjEn"] == "Disabled"
        assert _http(base, BIOS_SETTINGS, "PATCH",
                     {"Attributes": {"AEPErrorInjEn": "Enabled"}})[0] == 202
        assert _http(base, BIOS)[1]["Attributes"]["AEPErrorInjEn"] == "Disabled"  # pending only
        assert _http(base, RESET, "POST", {"ResetType": "GracefulRestart"})[0] == 204
        assert _http(base, BIOS)[1]["Attributes"]["AEPErrorInjEn"] == "Enabled"


def test_dell_log_clear_and_volume_lifecycle_return_accepted() -> None:
    """SEL clear, volume create, and volume delete are accepted by the mock."""
    module = _load_server_module()
    with module.run_server("127.0.0.1", 0, DELL_CORPUS, mutation_rules=DELL_RULES) as srv:
        base = "http://{}:{}".format(*srv.server_address)
        assert _http(base, SEL_CLEAR, "POST", {})[0] == 204
        assert _http(base, VOLUMES, "POST", {"Name": "vol0", "RAIDType": "None"})[0] == 202
        # glob rule matches any volume member id
        assert _http(base, f"{VOLUMES}/PCIeSSD.Integrated.1-0", "DELETE")[0] == 202


def test_dell_rules_every_path_resolves_to_a_corpus_fixture() -> None:
    """Each rule's precondition/transition path exists in the committed corpus."""
    module = _load_server_module()
    spec = yaml.safe_load(DELL_RULES.read_text(encoding="utf-8"))
    assert spec["vendor"] == "dell-xr8620t"
    for rule in spec["rules"]:
        paths = [t["path"] for t in rule.get("state_transitions", [])]
        paths += [c["path"] for c in rule.get("when", [])]
        for resource_path in paths:
            assert module.fixture_for_redfish_path(DELL_CORPUS, resource_path) is not None, (
                f"rule {rule['name']} targets missing {resource_path}"
            )
