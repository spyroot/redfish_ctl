"""Per-instance identity isolation for many mock BMCs at once.

One committed corpus serves as many DISTINCT BMCs via a per-pod identity overlay
(``MOCK_BMC_RACK`` / ``MOCK_BMC_SLOT``), captured when each handler class is
built. This test starts several servers on distinct ports, each with its own
rack/slot, then concurrently reads ``System_0`` from all of them and asserts each
returns ONLY its own ``GB300-R<rack>-S<slot>`` identity — no overlay from a peer
instance leaks across servers under concurrent load.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_MODULE = REPO_ROOT / "k8s" / "sandbox" / "mock_bmc_server.py"

SYSTEM = "/redfish/v1/Systems/System_0"
# Distinct (rack, slot) identities -> distinct GB300-R<rack>-S<slot> nodes.
CONFIGS = ((1, 1), (2, 1), (3, 2), (4, 3), (5, 4), (6, 1), (7, 2), (8, 3))
REQUESTS_PER_INSTANCE = 30


def _load_server_module() -> Any:
    spec = importlib.util.spec_from_file_location("mock_bmc_server", SERVER_MODULE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "_redfish_v1.json").write_text(
        json.dumps({"@odata.id": "/redfish/v1", "Id": "RootService"}), encoding="utf-8"
    )
    (corpus / "_redfish_v1_Systems_System_0.json").write_text(
        json.dumps(
            {
                "@odata.id": SYSTEM,
                "Id": "System_0",
                "Name": "shared-corpus",
                "SerialNumber": "shared-corpus",
                "PowerState": "On",
            }
        ),
        encoding="utf-8",
    )
    return corpus


def _get_serial(base: str) -> str:
    with urllib.request.urlopen(base + SYSTEM, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload["SerialNumber"]


def _expected_node(rack: int, slot: int) -> str:
    return f"GB300-R{rack}-S{str(slot).zfill(2)}"


def test_multi_instance_identity_overlays_do_not_leak(tmp_path: Path) -> None:
    """Concurrent reads across instances each see only their own identity."""
    module = _load_server_module()
    corpus = _corpus(tmp_path)
    saved = {k: os.environ.get(k) for k in ("MOCK_BMC_RACK", "MOCK_BMC_SLOT")}

    try:
        with contextlib.ExitStack() as stack:
            instances: list[tuple[str, str]] = []  # (base_url, expected_serial)
            for rack, slot in CONFIGS:
                # Each handler class captures the CURRENT env at build time, so
                # set identity immediately before starting each server.
                os.environ["MOCK_BMC_RACK"] = str(rack)
                os.environ["MOCK_BMC_SLOT"] = str(slot)
                server = stack.enter_context(module.run_server("127.0.0.1", 0, corpus))
                base = "http://{}:{}".format(*server.server_address)
                instances.append((base, _expected_node(rack, slot)))

            # Every distinct identity actually rendered (guards a build-time bug).
            assert len({serial for _, serial in instances}) == len(CONFIGS)

            plan = [inst for inst in instances for _ in range(REQUESTS_PER_INSTANCE)]
            with ThreadPoolExecutor(max_workers=len(CONFIGS) * 4) as pool:
                observed = list(
                    pool.map(lambda inst: (_get_serial(inst[0]), inst[1]), plan)
                )
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    assert len(observed) == len(CONFIGS) * REQUESTS_PER_INSTANCE
    # Each read returns exactly its own instance's identity — no cross-leak.
    for got_serial, expected_serial in observed:
        assert got_serial == expected_serial
