"""Contracts for the Supermicro X10 (ASPEED BMC) vendor corpus + mutation rules.

The corpus under corpora/mock/supermicro_x10sdv.tar.gz is a real capture from a home-lab
X10 board. The crawl did not capture the ServiceRoot, so a minimal synthetic
_redfish_v1.json links the collections that WERE captured (and is marked as
synthesized); everything else is the untouched capture. This board exposes no
Bios and only a proprietary VirtualMedia, so the rules cover power/boot/log.

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
X10_CORPUS = corpus_dir(REPO_ROOT / "tests" / "supermicro_x10_corpus.tar.gz", "192.168.254.120")
X10_RULES = REPO_ROOT / "tests" / "mutation_rules" / "supermicro_x10.yaml"

SYSTEM = "/redfish/v1/Systems/1"
RESET = f"{SYSTEM}/Actions/ComputerSystem.Reset"
LOG_CLEAR = f"{SYSTEM}/LogServices/Log1/Actions/LogService.ClearLog"


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


def test_x10_corpus_is_discoverable_via_a_synthesized_root() -> None:
    """The corpus serves as a Supermicro X10; the synthetic root is marked as such."""
    module = _load_server_module()
    with module.run_server("127.0.0.1", 0, X10_CORPUS) as srv:
        base = "http://{}:{}".format(*srv.server_address)
        _, root = _http(base, "/redfish/v1")
        assert root["Vendor"] == "Supermicro"
        # The synthetic root is transparently flagged, never passed off as captured.
        assert root["Oem"]["redfish_ctl"]["Synthesized"] is True
        members = [m["@odata.id"] for m in _http(base, "/redfish/v1/Systems")[1]["Members"]]
        assert SYSTEM in members
        _, system = _http(base, SYSTEM)
        assert system["Manufacturer"] == "Supermicro"
        assert system["PowerState"] == "On"


def test_x10_power_boot_and_log_mutations() -> None:
    """Power cycle, one-time boot revert, and log clear reconcile on the corpus."""
    module = _load_server_module()
    with module.run_server("127.0.0.1", 0, X10_CORPUS, mutation_rules=X10_RULES) as srv:
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

        assert _http(base, LOG_CLEAR, "POST", {})[0] == 204


def test_x10_rules_every_path_resolves_to_a_corpus_fixture() -> None:
    """Each rule's precondition/transition path exists in the committed corpus."""
    module = _load_server_module()
    spec = yaml.safe_load(X10_RULES.read_text(encoding="utf-8"))
    assert spec["vendor"] == "supermicro-x10"
    for rule in spec["rules"]:
        paths = [t["path"] for t in rule.get("state_transitions", [])]
        paths += [c["path"] for c in rule.get("when", [])]
        for resource_path in paths:
            assert module.fixture_for_redfish_path(X10_CORPUS, resource_path) is not None, (
                f"rule {rule['name']} targets missing {resource_path}"
            )
