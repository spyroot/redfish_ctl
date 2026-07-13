"""BMC-safety tests: prove a crawl cannot 'nuke' a fragile BMC.

A live GB300 HGX BMC's embedded HTTPS server *wedged* (stopped answering on 443)
when the old client opened a fresh TCP+TLS connection for every resource — a full
crawl is hundreds of handshakes, and the small BMC web server fell over. The fix
routes every GET through a cached keep-alive ``requests.Session`` with a bounded
connection pool (see ``RedfishManager._http_session``), so one crawl reuses a
handful of connections instead of opening one per request.

These tests stand up a tiny loopback HTTP server that models that failure mode:
it counts how many TCP connections the client actually opens and *wedges* (drops
new connections) once a small budget is exceeded — exactly what the real BMC did.
A many-request "crawl" must then complete fully **without** tripping the wedge,
which is only possible if the client reuses connections. No external network, no
iDRAC, no REDFISH_IP required.

Author Mus spyroot@gmail.com
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from redfish_ctl.base_manager import CommandBase
from redfish_ctl.redfish_manager import RedfishManager


def _start_fragile_bmc(max_connections):
    """Start a loopback HTTP server that wedges past ``max_connections``.

    Returns ``(server, thread, state, base_url)``. ``state`` tracks the number of
    distinct TCP connections (``setup`` runs once per connection), served requests,
    and whether the connection budget was ever exceeded (``wedged``). Keep-alive is
    on (HTTP/1.1 + Content-Length) so one connection can serve many GETs.
    """
    state = {"connections": 0, "requests": 0, "wedged": False}
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"  # keep-alive: one conn serves many requests

        def setup(self):
            super().setup()
            with lock:
                state["connections"] += 1
                over_budget = state["connections"] > max_connections
                if over_budget:
                    state["wedged"] = True
            if over_budget:
                # Simulate the fragile BMC dropping every new connection once it
                # is overwhelmed (the SSL UNEXPECTED_EOF / HTTP 000 we saw live).
                try:
                    self.connection.close()
                finally:
                    raise OSError("fragile BMC wedged: too many new connections")

        def do_GET(self):
            with lock:
                state["requests"] += 1
            body = json.dumps({"@odata.id": self.path}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # keep the test output clean
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, thread, state, f"http://{host}:{port}"


@pytest.mark.parametrize("manager_cls", [RedfishManager, CommandBase])
def test_full_crawl_stays_within_fragile_bmc_budget(manager_cls, monkeypatch):
    """Many sequential GETs (a 'full dump') never exceed the BMC's connection budget.

    The server wedges after 3 new connections. With connection reuse, N=50 GETs
    ride one pooled keep-alive connection, so the budget is never hit and every
    request succeeds — the crawl gets a full dump without nuking the BMC. If reuse
    regressed to one-connection-per-request, the 4th GET would wedge the server.
    """
    monkeypatch.setenv("REDFISH_HTTP_POOL", "2")
    budget = 3
    server, thread, state, base = _start_fragile_bmc(max_connections=budget)
    try:
        mgr = manager_cls()
        n_requests = 50
        for i in range(n_requests):
            resp = mgr.api_get_call(f"{base}/redfish/v1/R{i}", hdr=None)
            assert resp.status_code == 200

        assert state["requests"] == n_requests, "not every resource was fetched"
        assert not state["wedged"], (
            "crawl exceeded the connection budget -> this would wedge a real BMC"
        )
        assert state["connections"] <= budget, (
            f"opened {state['connections']} connections for {n_requests} GETs "
            "-- keep-alive reuse is broken"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("manager_cls", [RedfishManager, CommandBase])
def test_crawl_reuses_a_single_connection_for_many_requests(manager_cls, monkeypatch):
    """With a generous budget, a burst of GETs still collapses onto ~1 connection.

    Directly measures the anti-nuke property: connection count stays tiny and flat
    as request count grows, instead of climbing one-per-request.
    """
    monkeypatch.setenv("REDFISH_HTTP_POOL", "4")
    server, thread, state, base = _start_fragile_bmc(max_connections=1000)
    try:
        mgr = manager_cls()
        for i in range(100):
            assert mgr.api_get_call(f"{base}/redfish/v1/R{i}", hdr=None).status_code == 200
        assert state["requests"] == 100
        # 100 requests, but only a handful of TCP connections thanks to keep-alive.
        assert state["connections"] <= 4, (
            f"{state['connections']} connections for 100 requests -- not reusing"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
