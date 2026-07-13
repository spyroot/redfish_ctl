"""Contract for the primary Supermicro GB300 corpus (packed as an LFS tarball).

The GB300 mock-ready artifact ships as ``corpora/mock/supermicro_gb300.tar.gz`` (built
by ``tools/pack_corpus.py``, tracked by Git LFS) and is the corpus the telemetry,
controller, and mutation tests replay. This pins that the packed corpus still
serves as a Supermicro GB300, that every extracted fixture is valid JSON, and
that the BIOS attribute registry survived the device+telemetry filter — the
profiles test in ``test_bios_profiles_specs.py`` reads that registry straight
from here.

Author Mus <spyroot@gmail.com>
"""
from __future__ import annotations

import importlib.util
import json
import urllib.request
from pathlib import Path

from vendor_corpus import corpus_dir

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_MODULE = REPO_ROOT / "k8s" / "sandbox" / "mock_bmc_server.py"
CORPUS = corpus_dir(REPO_ROOT / "tests" / "supermicro_gb300_corpus.tar.gz", "172.25.230.37")

SYSTEM = "/redfish/v1/Systems/System_0"
BIOS_REGISTRY = "_redfish_v1_Registries_BiosAttributeRegistry_BiosAttributeRegistry.json"


def _load_server_module():
    spec = importlib.util.spec_from_file_location("mock_bmc_server", SERVER_MODULE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _get(base: str, path: str):
    with urllib.request.urlopen(base + path, timeout=5) as response:
        raw = response.read().decode("utf-8")
        return response.status, (json.loads(raw) if raw else None)


def test_gb300_corpus_serves_as_a_supermicro_gb300() -> None:
    """The packed corpus serves as a Supermicro GB NVL (ServiceRoot + System_0)."""
    module = _load_server_module()
    with module.run_server("127.0.0.1", 0, CORPUS) as srv:
        base = "http://{}:{}".format(*srv.server_address)

        _, root = _get(base, "/redfish/v1")
        assert root["Vendor"] == "Supermicro"
        assert root["RedfishVersion"] == "1.17.0"

        _, system = _get(base, SYSTEM)
        assert system["Manufacturer"] == "Supermicro"
        assert system["Model"] == "GB NVL"
        assert system["PowerState"] == "On"


def test_gb300_corpus_json_artifacts_parse() -> None:
    """Every fixture extracted from the tarball is syntactically valid JSON."""
    bad = []
    for path in sorted(CORPUS.glob("*.json")):
        try:
            json.loads(path.read_text())
        except ValueError as exc:
            bad.append(f"{path.name}: {exc}")
    assert bad == []


def test_gb300_corpus_keeps_the_bios_attribute_registry() -> None:
    """The device+telemetry filter keeps the BIOS attribute registry (device data).

    Unlike the generic DMTF message registries the filter drops, the BIOS
    attribute registry defines this box's real BIOS knobs, and
    ``test_bios_profiles_specs.py`` validates the supermicro profiles against it,
    so packing must not drop it.
    """
    registry = CORPUS / BIOS_REGISTRY
    assert registry.is_file()
    entries = json.loads(registry.read_text())["RegistryEntries"]["Attributes"]
    assert len(entries) > 0
