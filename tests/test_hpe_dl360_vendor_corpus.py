"""Contracts for the HPE iLO 5 (ProLiant DL360 Gen10) vendor corpus + rules.

The corpus under tests/hpe_dl360_corpus is a real iLO 5 capture. The crawl did
not capture the ServiceRoot or the Systems/Managers collection index files, so
minimal synthetic index files (flagged Oem.redfish_ctl.Synthesized) link the
captured members; everything else is untouched. HPE mixes case in its own
@odata.ids (Systems/1 but lowercase systems/1/bios), which the rules mirror.

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
HPE_CORPUS = corpus_dir(REPO_ROOT / "tests" / "hpe_dl360_corpus.tar.gz", "10.43.3.209")
HPE_RULES = REPO_ROOT / "tests" / "mutation_rules" / "hpe_dl360.yaml"

SYSTEM = "/redfish/v1/Systems/1"
RESET = f"{SYSTEM}/Actions/ComputerSystem.Reset"
BIOS = "/redfish/v1/systems/1/bios"
BIOS_SETTINGS = "/redfish/v1/systems/1/bios/settings"
IML_CLEAR = f"{SYSTEM}/LogServices/IML/Actions/LogService.ClearLog"


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


def test_hpe_corpus_is_discoverable_as_ilo5() -> None:
    """The corpus serves as an HPE iLO 5 DL360 through the synthesized root."""
    module = _load_server_module()
    with module.run_server("127.0.0.1", 0, HPE_CORPUS) as srv:
        base = "http://{}:{}".format(*srv.server_address)
        _, root = _http(base, "/redfish/v1")
        assert root["Vendor"] == "HPE"
        assert root["Oem"]["redfish_ctl"]["Synthesized"] is True
        members = [m["@odata.id"] for m in _http(base, "/redfish/v1/Systems")[1]["Members"]]
        assert SYSTEM in members
        _, system = _http(base, SYSTEM)
        assert system["Manufacturer"] == "HPE"
        assert system["Model"] == "ProLiant DL360 Gen10"
        assert system["PowerState"] == "On"


def test_hpe_power_boot_and_bios_mutations() -> None:
    """Power cycle, one-time boot revert, and staged-BIOS apply reconcile."""
    module = _load_server_module()
    with module.run_server("127.0.0.1", 0, HPE_CORPUS, mutation_rules=HPE_RULES) as srv:
        base = "http://{}:{}".format(*srv.server_address)

        assert _http(base, RESET, "POST", {"ResetType": "ForceOff"})[0] == 204
        assert _http(base, SYSTEM)[1]["PowerState"] == "Off"
        assert _http(base, RESET, "POST", {"ResetType": "On"})[0] == 204
        assert _http(base, SYSTEM)[1]["PowerState"] == "On"

        assert _http(base, SYSTEM, "PATCH",
                     {"Boot": {"BootSourceOverrideTarget": "Pxe",
                               "BootSourceOverrideEnabled": "Once"}})[0] == 200
        assert _http(base, SYSTEM)[1]["Boot"]["BootSourceOverrideEnabled"] == "Once"
        assert _http(base, RESET, "POST", {"ResetType": "GracefulRestart"})[0] == 204
        assert _http(base, SYSTEM)[1]["Boot"]["BootSourceOverrideEnabled"] == "Disabled"

        # BIOS BootMode staged at the lowercase bios/settings, applied on reset.
        assert _http(base, BIOS)[1]["Attributes"]["BootMode"] == "Uefi"
        assert _http(base, BIOS_SETTINGS, "PATCH",
                     {"Attributes": {"BootMode": "LegacyBios"}})[0] == 200
        assert _http(base, BIOS)[1]["Attributes"]["BootMode"] == "Uefi"  # pending only
        assert _http(base, RESET, "POST", {"ResetType": "GracefulRestart"})[0] == 204
        assert _http(base, BIOS)[1]["Attributes"]["BootMode"] == "LegacyBios"

        assert _http(base, IML_CLEAR, "POST", {})[0] == 204


def test_hpe_rules_every_path_resolves_to_a_corpus_fixture() -> None:
    """Each rule's precondition/transition path exists in the committed corpus."""
    module = _load_server_module()
    spec = yaml.safe_load(HPE_RULES.read_text(encoding="utf-8"))
    assert spec["vendor"] == "hpe-dl360"
    for rule in spec["rules"]:
        paths = [t["path"] for t in rule.get("state_transitions", [])]
        paths += [c["path"] for c in rule.get("when", [])]
        for resource_path in paths:
            assert module.fixture_for_redfish_path(HPE_CORPUS, resource_path) is not None, (
                f"rule {rule['name']} targets missing {resource_path}"
            )
