"""Offline tests for the redfish_ctl web explorer.

The explorer serves a tree of read-only commands and invokes them through the
tool's own registry. These tests pin the catalog allow-list, the invoke
dispatch, and the server-rendered page — with no live BMC (a fake manager for
unit dispatch, the committed GB300 corpus for an end-to-end command run).
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import pytest
from vendor_corpus import corpus_dir

from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.webui import catalog, server

GB300_CORPUS = corpus_dir(
    Path(__file__).parent.parent / "supermicro_gb300_corpus.tar.gz",
    "172.25.230.37",
)
GB300_INDEX = {p.name.lower(): p for p in GB300_CORPUS.glob("*.json")}

# Known mutating command names that must NEVER appear in the read-only catalog.
# An explicit denylist beats substring matching, which false-positives on read
# commands like update_service and metric-definitions.
_MUTATING_COMMANDS = frozenset({
    "system_reset", "reboot", "manager_reset", "bios_change_settings", "attribute_update",
    "change_boot_order", "boot_one_shot", "update", "clear_pending", "vm-mount", "ntp-set",
    "firmware-update", "volume-create", "volume-delete", "convert_none_raid", "boot_enable",
    "account-create", "account-update", "account-delete", "account-import-sshkey",
    "serial-console", "secure-boot-enable", "virtual_disk_insert", "virtual_disk_eject",
    "job_apply", "job_del", "import_sysconfig", "bios_snapshot",
})


class _FakeManager:
    """Records sync_invoke calls and returns a configured CommandResult."""

    def __init__(self, result):
        self._result = result
        self.calls = []

    def sync_invoke(self, api_call, name, **kwargs):
        self.calls.append((api_call, name, kwargs))
        return self._result


def test_catalog_resolves_and_is_read_only():
    """Every catalog command resolves and none is a mutating action."""
    entries = [e for _d, cmds in catalog.CATALOG for e in cmds]
    assert entries, "catalog must not be empty"
    for entry in entries:
        assert catalog.resolve(entry.command) is entry
        assert entry.command not in _MUTATING_COMMANDS, (
            f"catalog command {entry.command!r} is mutating; explorer is read-only"
        )


def test_catalog_json_shape():
    """catalog_json yields domains with labelled command entries for the UI."""
    data = catalog.catalog_json()
    assert {d["domain"] for d in data} >= {"Network", "Firmware", "System"}
    nic = next(c for d in data for c in d["commands"] if c["command"] == "nic-firmware")
    assert nic["api"] == "NicFirmware"
    assert nic["heavy"] is False


def test_invoke_command_success_and_error():
    """invoke_command unwraps CommandResult into ok/data or ok=False/error."""
    ok_mgr = _FakeManager(CommandResult({"adapters": []}, None, None, None))
    out = server.invoke_command(ok_mgr, "nic-firmware")
    assert out == {"ok": True, "data": {"adapters": []}}
    # The dispatch used the catalog's (api, name) pair.
    api_call, name, _ = ok_mgr.calls[0]
    assert name == "nic-firmware" and api_call.name == "NicFirmware"

    err_mgr = _FakeManager(CommandResult(None, None, None, "boom"))
    out = server.invoke_command(err_mgr, "nic-firmware")
    assert out["ok"] is False and out["error"] == "boom"


def test_invoke_command_rejects_unlisted_command():
    """A command not in the read-only catalog is refused (never dispatched)."""
    mgr = _FakeManager(CommandResult({}, None, None, None))
    with pytest.raises(KeyError):
        server.invoke_command(mgr, "system_reset")
    assert mgr.calls == []  # nothing was invoked


def test_render_page_lists_domains_and_commands_and_escapes():
    """The rendered explorer shell carries the tree and marks heavy commands."""
    html = server.render_page("mock-gb300:443")
    assert "redfish_ctl explorer" in html
    assert "NIC / DPU firmware" in html
    assert 'data-cmd="nic-firmware"' in html
    assert "Sensors" in html and 'data-heavy="1"' in html  # sensors flagged heavy
    assert "mock-gb300:443" in html


def test_invoke_nic_firmware_end_to_end_against_corpus():
    """Explorer invoke path drives the real nic-firmware command over the corpus."""
    requests_mock = pytest.importorskip("requests_mock")

    def cb(request, context):
        name = "_" + request.path.strip("/").replace("/", "_") + ".json"
        fixture = GB300_INDEX.get(name.lower())
        if fixture is None:
            context.status_code = 404
            return json.dumps({"error": "no fixture"})
        context.status_code = 200
        return fixture.read_text()

    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=cb)
        manager = IDracManager(
            idrac_ip="mock-gb300", idrac_username="root", idrac_password="x",
            insecure=True, is_debug=False,
        )
        out = server.invoke_command(manager, "nic-firmware")

    assert out["ok"] is True
    summary = out["data"]["summary"]
    assert summary["nic_count"] == 4
    assert "40.45.3048" in summary["distinct_versions"]


# --------------------------------------------------------------------------- #
# HTTP handler: route + status-code contract (loopback server, no live BMC).
# --------------------------------------------------------------------------- #


class _RaisingManager:
    """sync_invoke raises — exercises the do_POST backend-failure (502) path."""

    def sync_invoke(self, *_a, **_k):
        raise RuntimeError("bmc unreachable")


@contextmanager
def _serve(manager):
    """Run the explorer on an ephemeral loopback port with a fixed manager."""
    handler = server._Handler
    handler.manager = manager  # pre-set so the real factory is never called
    handler.manager_factory = staticmethod(lambda: manager)
    handler.target_label = "test-bmc:443"
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        handler.manager = None


def _req(url, *, data=None, method="GET"):
    """Return (status, body-bytes), treating a 4xx/5xx as a normal response."""
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_http_get_routes_status_codes():
    """/healthz, /api/catalog, and / serve 200; an unknown path is 404."""
    with _serve(_FakeManager(CommandResult({"adapters": []}, None, None, None))) as base:
        assert _req(base + "/healthz")[0] == 200
        assert _req(base + "/api/catalog")[0] == 200
        code, body = _req(base + "/")
        assert code == 200 and b"redfish_ctl explorer" in body
        assert _req(base + "/nope")[0] == 404


def test_http_post_invoke_success_and_input_errors():
    """A valid command is 200/ok; bad JSON and an unlisted command are 400; wrong path 404."""
    with _serve(_FakeManager(CommandResult({"adapters": []}, None, None, None))) as base:
        code, body = _req(base + "/api/invoke", data=json.dumps({"command": "nic-firmware"}).encode(),
                          method="POST")
        assert code == 200 and json.loads(body)["ok"] is True
        # A mutating command is not allow-listed -> 400, never dispatched.
        assert _req(base + "/api/invoke", data=json.dumps({"command": "system_reset"}).encode(),
                    method="POST")[0] == 400
        # Malformed body -> 400.
        assert _req(base + "/api/invoke", data=b"{not json", method="POST")[0] == 400
        # Wrong POST path -> 404.
        assert _req(base + "/other", data=b"{}", method="POST")[0] == 404


def test_http_post_backend_failure_is_502():
    """A transport/backend failure during invoke surfaces as 502, not a masked 200."""
    with _serve(_RaisingManager()) as base:
        code, body = _req(base + "/api/invoke", data=json.dumps({"command": "nic-firmware"}).encode(),
                          method="POST")
        assert code == 502
        assert json.loads(body)["ok"] is False
