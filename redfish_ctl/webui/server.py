"""HTTP explorer server: a tree of redfish_ctl read commands, invoked live.

Selecting a command in the tree POSTs to ``/api/invoke``, which dispatches the
real command through the tool's registry (``sync_invoke``) against the configured
BMC and returns the command's JSON result. Only allow-listed read commands from
``catalog.py`` can be invoked, so the explorer is read-only by construction.
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from ..idrac_manager import IDracManager
from .catalog import CATALOG, catalog_json, resolve


def _env_first(*names: str, default: str = "") -> str:
    """Return the first set environment variable value (REDFISH_* before IDRAC_*).

    :param default: value returned when none of ``names`` is set.
    :return: the first non-empty environment value, or ``default``.
    """
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


def invoke_command(manager: Any, command: str, **kwargs: Any) -> dict[str, Any]:
    """Invoke an allow-listed read command through the tool registry.

    Only allow-listed read commands are dispatched; an unknown name raises KeyError
    as a guard against invoking any mutating action.

    :param manager: IDracManager used to dispatch the command.
    :param command: allow-listed read command name from the catalog.
    :return: ``{"ok": True, "data": ...}`` on success, or
        ``{"ok": False, "error": ..., "data": ...}`` on a command error.
    :raises KeyError: if ``command`` is not in the read-only catalog.
    """
    entry = resolve(command)
    if entry is None:
        raise KeyError(command)
    result = manager.sync_invoke(entry.api, entry.command, **kwargs)
    if getattr(result, "error", None):
        return {"ok": False, "error": str(result.error), "data": getattr(result, "data", None)}
    return {"ok": True, "data": getattr(result, "data", None)}


def _esc(value: Any) -> str:
    """Escape HTML-special characters in ``value`` for safe embedding in markup.

    :param value: value to stringify and escape.
    :return: the string with ``&``, ``<``, ``>``, and ``"`` replaced by entities.
    """
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_page(target_label: str) -> str:
    """Server-render the explorer shell with the command tree from the catalog.

    :param target_label: BMC address label shown in the page header.
    :return: the full HTML page as a string.
    """
    tree = []
    for domain, entries in CATALOG:
        items = "".join(
            f'<li class="cmd" data-cmd="{_esc(e.command)}" data-heavy="{"1" if e.heavy else "0"}" '
            f'title="{_esc(e.description)}">'
            f'<span class="lbl">{_esc(e.label)}</span>'
            f'{"<span class=hv>slow</span>" if e.heavy else ""}'
            f'<span class="api">{_esc(e.command)}</span></li>'
            for e in entries
        )
        tree.append(
            f'<li class="domain"><div class="dh">{_esc(domain)}</div><ul>{items}</ul></li>'
        )
    tree_html = "\n".join(tree)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>redfish_ctl explorer</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font-family: -apple-system, Segoe UI, Roboto, sans-serif;
    background: #0f1115; color: #e6e6e6; display: grid; grid-template-columns: 320px 1fr;
    grid-template-rows: auto 1fr; height: 100vh; }}
  header {{ grid-column: 1 / 3; padding: 14px 22px; border-bottom: 1px solid #262a33;
    display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap; }}
  h1 {{ font-size: 16px; margin: 0; font-weight: 600; }}
  .sub {{ color: #8b93a7; font-size: 12px; }}
  .sub code {{ color: #60a5fa; }}
  nav {{ overflow-y: auto; border-right: 1px solid #262a33; padding: 8px 0; }}
  nav ul {{ list-style: none; margin: 0; padding: 0; }}
  .dh {{ padding: 10px 18px 4px; font-size: 11px; text-transform: uppercase;
    letter-spacing: .06em; color: #8b93a7; font-weight: 700; }}
  li.cmd {{ padding: 7px 18px; cursor: pointer; display: flex; align-items: baseline; gap: 8px;
    border-left: 2px solid transparent; }}
  li.cmd:hover {{ background: #171a21; }}
  li.cmd.active {{ background: #171a21; border-left-color: #60a5fa; }}
  li.cmd .lbl {{ font-size: 13px; }}
  li.cmd .api {{ margin-left: auto; font-family: ui-monospace, monospace; font-size: 11px; color: #6b7280; }}
  li.cmd .hv {{ font-size: 9px; background: #3a2f12; color: #fbbf24; padding: 1px 5px;
    border-radius: 4px; text-transform: uppercase; }}
  main {{ overflow: auto; padding: 18px 22px; }}
  .meta {{ font-size: 12px; color: #8b93a7; margin-bottom: 12px; }}
  .meta b {{ color: #e6e6e6; }}
  .call {{ font-family: ui-monospace, monospace; font-size: 12px; color: #c9d1d9;
    background: #171a21; border: 1px solid #262a33; border-radius: 8px; padding: 10px 12px;
    margin-bottom: 12px; white-space: pre-wrap; }}
  pre {{ background: #171a21; border: 1px solid #262a33; border-radius: 8px; padding: 14px;
    overflow: auto; font-size: 12px; line-height: 1.5; margin: 0; }}
  .err {{ color: #f87171; }}
  .placeholder {{ color: #8b93a7; margin-top: 40px; text-align: center; }}
  .spin {{ color: #fbbf24; }}
</style>
</head>
<body>
<header>
  <h1>redfish_ctl explorer</h1>
  <span class="sub">target BMC <b>{_esc(target_label)}</b> &middot; every selection runs
    <code>redfish_ctl &lt;command&gt;</code> live via <code>sync_invoke</code> — read-only</span>
</header>
<nav><ul>
{tree_html}
</ul></nav>
<main id="out">
  <div class="placeholder">Select a command on the left to invoke it against the BMC.</div>
</main>
<script>
  const out = document.getElementById('out');
  let active = null;
  async function invoke(li) {{
    const cmd = li.dataset.cmd;
    const heavy = li.dataset.heavy === '1';
    if (active) active.classList.remove('active');
    active = li; li.classList.add('active');
    out.innerHTML = '<div class="placeholder spin">Invoking redfish_ctl ' + cmd +
      (heavy ? ' … (this command does a full walk and may take a while)' : ' …') + '</div>';
    const t0 = performance.now();
    try {{
      const r = await fetch('/api/invoke', {{
        method: 'POST', headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{command: cmd}})
      }});
      const body = await r.json();
      const ms = Math.round(performance.now() - t0);
      const data = JSON.stringify(body.data, null, 2);
      const errCls = body.ok ? '' : ' err';
      out.innerHTML =
        '<div class="meta"><b>' + cmd + '</b> &middot; api ' + body.api +
        ' &middot; ' + ms + ' ms &middot; ' + (body.ok ? 'ok' : '<span class=err>error</span>') + '</div>' +
        '<div class="call">redfish_ctl ' + cmd + '\\n' +
        '  → manager.sync_invoke(ApiRequestType.' + body.api + ', "' + cmd + '")</div>' +
        (body.ok ? '' : '<pre class="err">' + escapeHtml(body.error) + '</pre>') +
        '<pre class="' + errCls + '">' + escapeHtml(data) + '</pre>';
    }} catch (e) {{
      out.innerHTML = '<pre class="err">' + escapeHtml(String(e)) + '</pre>';
    }}
  }}
  function escapeHtml(s) {{
    return String(s).replace(/[&<>]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;'}}[c]));
  }}
  document.querySelectorAll('li.cmd').forEach(li =>
    li.addEventListener('click', () => invoke(li)));
</script>
</body>
</html>
"""


class _Handler(BaseHTTPRequestHandler):
    server_version = "redfish-ctl-explorer/1.0"
    manager: Any = None
    manager_lock = threading.Lock()
    # The shared manager wraps a single requests.Session, which is NOT
    # thread-safe. ``manager_lock`` only guards the lazy build; ``invoke_lock``
    # serializes the actual command invocations so concurrent POSTs can't
    # interleave on that session and return each other's payloads.
    invoke_lock = threading.Lock()
    manager_factory = None  # callable[[], IDracManager]
    target_label = ""

    def log_message(self, *_a: Any) -> None:
        """Silence the default per-request logging of BaseHTTPRequestHandler."""
        return

    def _get_manager(self) -> Any:
        """Return the shared manager, building it once under the manager lock.

        :return: the lazily constructed IDracManager instance.
        """
        with self.manager_lock:
            if self.__class__.manager is None:
                self.__class__.manager = self.__class__.manager_factory()
            return self.__class__.manager

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        """Write an HTTP response with the given status, body, and no-store headers.

        :param code: HTTP status code.
        :param body: response body bytes (skipped for HEAD requests).
        :param content_type: value for the ``Content-Type`` header.
        """
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        """Route GET/HEAD requests to healthz, catalog JSON, the page, or 404."""
        path = urlsplit(self.path).path.rstrip("/") or "/"
        if path == "/healthz":
            self._send(200, b'{"status":"ok"}', "application/json")
            return
        if path == "/api/catalog":
            self._send(200, json.dumps(catalog_json()).encode(), "application/json")
            return
        if path == "/":
            self._send(200, render_page(self.target_label).encode("utf-8"),
                       "text/html; charset=utf-8")
            return
        self._send(404, b'{"error":"not found"}', "application/json")

    do_HEAD = do_GET

    def do_POST(self) -> None:  # noqa: N802
        """Invoke the requested allow-listed read command and return its JSON result.

        Serves only ``/api/invoke``; rejects unknown paths, bad JSON, and
        non-allow-listed commands, and returns 502 on a transport/backend failure.
        """
        if urlsplit(self.path).path.rstrip("/") != "/api/invoke":
            self._send(404, b'{"error":"not found"}', "application/json")
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send(400, b'{"error":"bad json"}', "application/json")
            return
        command = str(payload.get("command") or "")
        entry = resolve(command)
        if entry is None:
            self._send(400, json.dumps({"error": f"command not allow-listed: {command}"}).encode(),
                       "application/json")
            return
        t0 = time.monotonic()
        try:
            manager = self._get_manager()
            # Serialize per-manager: the wrapped requests.Session is not
            # thread-safe, so only one command may be in flight at a time.
            with self.__class__.invoke_lock:
                result = invoke_command(manager, command)
            result["api"] = entry.api.name
            result["elapsedMs"] = int((time.monotonic() - t0) * 1000)
            self._send(200, json.dumps(result).encode(), "application/json")
        except Exception as exc:  # a transport/backend failure -> 502 Bad Gateway
            body = {"ok": False, "error": str(exc), "api": entry.api.name, "data": None}
            self._send(502, json.dumps(body).encode(), "application/json")


def make_manager_factory():
    """Build a factory that constructs the tool's IDracManager from env/flags.

    :return: tuple of (manager factory callable, ``"address:port"`` target label).
    """
    address = _env_first("REDFISH_IP", "IDRAC_IP")
    username = _env_first("REDFISH_USERNAME", "IDRAC_USERNAME", default="root")
    password = _env_first("REDFISH_PASSWORD", "IDRAC_PASSWORD")
    port = int(_env_first("REDFISH_PORT", "IDRAC_PORT", default="443"))
    scheme = _env_first("REDFISH_SCHEME", default="https")

    def factory() -> IDracManager:
        """Construct a IDracManager from the captured connection settings.

        :return: a new IDracManager for the configured BMC.
        """
        return IDracManager(
            idrac_ip=address,
            idrac_username=username,
            idrac_password=password,
            idrac_port=port,
            insecure=True,
            is_http=(scheme == "http"),
            is_debug=False,
        )

    return factory, f"{address}:{port}"


class ExplorerServer(ThreadingHTTPServer):
    """Threaded explorer server with a generous socket listen backlog.

    The stdlib default (``request_queue_size = 5``) drops connections at the OS
    accept queue when many clients connect at once, surfacing as resets/timeouts.
    128 lets a burst of explorer clients queue instead of being reset.
    """

    request_queue_size = 128
    daemon_threads = True


def run_server(bind_host: str = "0.0.0.0", bind_port: int = 8299) -> None:  # pragma: no cover
    """Serve the explorer, reading the target BMC from REDFISH_*/IDRAC_* env.

    :param bind_host: interface address to bind the HTTP server to.
    :param bind_port: TCP port to listen on.
    """
    factory, label = make_manager_factory()
    _Handler.manager_factory = staticmethod(factory)
    _Handler.target_label = label
    server = ExplorerServer((bind_host, bind_port), _Handler)
    print(f"redfish_ctl explorer on {bind_host}:{bind_port} -> BMC {label}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover
        pass
    finally:
        server.server_close()
