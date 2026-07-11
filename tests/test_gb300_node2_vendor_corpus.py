"""Contracts for the second GB300 node capture (172.25.230.20) + mutation rules.

This is a fuller capture of the same NVIDIA GB300 platform as the primary
committed corpus, taken from a different node. Its incremental value is the SEL
LogService ClearLog action, which the primary GB300 corpus did not capture, plus
virtual media. The crawl did not capture the ServiceRoot, so a minimal synthetic
_redfish_v1.json (flagged Oem.redfish_ctl.Synthesized) makes the corpus
discoverable.

Author Mus <spyroot@gmail.com>
"""
from __future__ import annotations

import importlib.util
import json
import urllib.error
import urllib.request
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_MODULE = REPO_ROOT / "k8s" / "sandbox" / "mock_bmc_server.py"
CORPUS = REPO_ROOT / "tests" / "nvidia_gb300_node2_corpus" / "json_responses" / "172.25.230.20"
RULES = REPO_ROOT / "tests" / "mutation_rules" / "nvidia_gb300_node2.yaml"

SYSTEM = "/redfish/v1/Systems/System_0"
RESET = f"{SYSTEM}/Actions/ComputerSystem.Reset"
USB1 = "/redfish/v1/Managers/BMC_0/VirtualMedia/USB1"
SEL_CLEAR = f"{SYSTEM}/LogServices/SEL/Actions/LogService.ClearLog"


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


def test_gb300_node2_serves_and_adds_sel_log_clear_and_vmedia() -> None:
    """The 2nd GB300 capture serves and covers SEL clear + virtual media + reset."""
    module = _load_server_module()
    with module.run_server("127.0.0.1", 0, CORPUS, mutation_rules=RULES) as srv:
        base = "http://{}:{}".format(*srv.server_address)

        _, root = _http(base, "/redfish/v1")
        assert root["Vendor"] == "Supermicro"
        assert root["Oem"]["redfish_ctl"]["Synthesized"] is True
        assert _http(base, SYSTEM)[1]["PowerState"] == "On"

        # reset works on this capture just like the primary GB300
        assert _http(base, RESET, "POST", {"ResetType": "ForceOff"})[0] == 204
        assert _http(base, SYSTEM)[1]["PowerState"] == "Off"

        # virtual media (captured on this node)
        assert _http(base, USB1)[1]["Inserted"] is False
        assert _http(base, f"{USB1}/Actions/VirtualMedia.InsertMedia", "POST", {"Image": "x"})[0] == 204
        assert _http(base, USB1)[1]["Inserted"] is True

        # the class the primary GB300 corpus lacked: SEL log clear
        assert _http(base, SEL_CLEAR, "POST", {})[0] == 204


def test_gb300_node2_rules_paths_resolve_to_fixtures() -> None:
    """Every rule's precondition/transition path exists in the committed corpus."""
    module = _load_server_module()
    spec = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    assert spec["vendor"] == "nvidia-gb300-node2"
    for rule in spec["rules"]:
        paths = [t["path"] for t in rule.get("state_transitions", [])]
        paths += [c["path"] for c in rule.get("when", [])]
        for resource_path in paths:
            assert module.fixture_for_redfish_path(CORPUS, resource_path) is not None, (
                f"rule {rule['name']} targets missing {resource_path}"
            )
