"""Concurrency contract for the read-only web explorer server.

The explorer shares ONE ``RedfishManagerBase`` across every request (lazily
built once, cached on the handler class). That manager wraps a
``requests.Session``, which is not thread-safe: two concurrent ``/api/invoke``
POSTs that both call ``sync_invoke`` on the same session can interleave and
return each other's payloads. The server must therefore serialize invocations
per manager. This test proves each concurrent command gets ITS OWN command's
data back — it fails against an unserialized handler and passes once the
per-manager invoke lock is in place.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from typing import Any

from redfish_ctl.webui import server as webui

# A handful of distinct read-only catalog commands to fan out over. Each maps to
# a unique (api, command) pair, so a mixed-up response is unambiguous.
_COMMANDS = (
    "system_query",
    "manager_query",
    "chassis_service_query",
    "boot_query",
    "power",
    "current_boot_query",
    "storage_list",
    "event-service",
)


class _RacyManager:
    """Fake manager that emulates a NON-thread-safe ``requests.Session``.

    It stashes the in-flight command on a shared instance attribute, yields the
    GIL, then reads the attribute back — so two unsynchronized concurrent calls
    clobber each other and observe the wrong command. A per-manager lock in the
    handler serializes the calls and restores correctness. The read delay only
    widens the interleaving window; it never blocks (no cross-thread barrier), so
    the serialized path cannot deadlock.
    """

    def __init__(self) -> None:
        self._inflight: str | None = None
        self.max_concurrency = 0
        self._active = 0
        self._counter_lock = threading.Lock()

    def sync_invoke(self, api: Any, command: str, **_kwargs: Any) -> SimpleNamespace:
        with self._counter_lock:
            self._active += 1
            self.max_concurrency = max(self.max_concurrency, self._active)
        try:
            self._inflight = command
            time.sleep(0.003)  # widen the window a racy session would corrupt
            observed = self._inflight
            return SimpleNamespace(
                error=None,
                data={"requested": command, "observed": observed, "api": getattr(api, "name", str(api))},
            )
        finally:
            with self._counter_lock:
                self._active -= 1


def _make_handler_class(manager: _RacyManager) -> type:
    """A fresh handler subclass bound to ``manager`` (isolates class state)."""

    class Handler(webui._Handler):
        pass

    Handler.manager = None
    Handler.manager_lock = threading.Lock()
    # invoke_lock is a class attribute added by the fix; give this subclass its
    # own so the test never contends with any other server instance.
    Handler.invoke_lock = threading.Lock()
    Handler.manager_factory = staticmethod(lambda: manager)
    Handler.target_label = "racy-mock"
    return Handler


def _post_invoke(base: str, command: str) -> dict[str, Any]:
    body = json.dumps({"command": command}).encode("utf-8")
    request = urllib.request.Request(
        base + "/api/invoke",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def test_webui_concurrent_invokes_keep_command_payloads_isolated() -> None:
    """Every concurrent /api/invoke returns its own command's data, not a peer's."""
    manager = _RacyManager()
    handler_cls = _make_handler_class(manager)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = "http://{}:{}".format(*server.server_address)
        jobs = [_COMMANDS[i % len(_COMMANDS)] for i in range(48)]
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            futures = {pool.submit(_post_invoke, base, cmd): cmd for cmd in jobs}
            results = [(futures[f], f.result()) for f in as_completed(futures)]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert len(results) == len(jobs)
    for requested, body in results:
        assert body["ok"] is True, body
        data = body["data"]
        # The response must be the manager's answer to THIS request, not a peer's
        # that clobbered a shared session mid-flight.
        assert data["requested"] == requested, body
        assert data["observed"] == requested, (
            f"payload mix-up: requested {requested!r} but session observed "
            f"{data['observed']!r} (concurrent sync_invoke race)"
        )
    # The lock must have actually serialized invocations against the one manager.
    assert manager.max_concurrency == 1, (
        f"expected serialized invokes, saw {manager.max_concurrency} concurrent"
    )
