"""Offline tests for the span-root ratchet gate.

The gate (tools/span_root_gate.py) flags direct HTTP transport calls that run
outside a tracing span and are not handed to a tracing wrapper. It must resolve
aliases and client instances while recognizing all three real tracing patterns
(with client_span; traced_request; and the async traced_request_callable).
Driven by parsing small source snippets, so it tests the AST logic directly.

Author Mus spyroot@gmail.com
"""
import ast

from tools import span_root_gate as gate


def _orphans(src: str) -> list[str]:
    """Return orphan line numbers the gate finds in a snippet.

    :param src: python source text.
    :return: ``["<mod>:<line>", ...]`` orphans.
    """
    out: list[str] = []
    gate._walk(ast.parse(src), False, "<mod>", out)
    return out


def test_bare_requests_get_is_orphan():
    """A raw requests.get outside any span is flagged."""
    assert _orphans("import requests\ndef f():\n    requests.get('u')\n")


def test_inline_client_span_is_traced():
    """requests.get inside a `with client_span` block is not flagged."""
    src = ("import requests\ndef f():\n"
           "    with tracing.client_span('u', 'GET'):\n"
           "        requests.get('u')\n")
    assert _orphans(src) == []


def test_partial_to_traced_request_is_traced():
    """A partial handed to traced_request (sync mutation path) is not flagged."""
    src = ("import requests, functools\ndef f():\n"
           "    call = functools.partial(requests.delete, 'u')\n"
           "    return tracing.traced_request('u', 'DELETE', call)\n")
    assert _orphans(src) == []


def test_partial_to_traced_request_callable_is_traced():
    """The async wrapper (traced_request_callable) also counts as traced."""
    src = ("import requests, functools\ndef f(loop):\n"
           "    call = functools.partial(requests.patch, 'u')\n"
           "    return loop.run_in_executor(None,\n"
           "        tracing.traced_request_callable('u', 'PATCH', call))\n")
    assert _orphans(src) == []


def test_partial_to_run_in_executor_without_wrapper_is_orphan():
    """The async-GET bug: a partial sent straight to run_in_executor with no
    wrapper is the genuine orphan the gate must catch."""
    src = ("import requests, functools\ndef f(loop):\n"
           "    return loop.run_in_executor(None,\n"
           "        functools.partial(requests.get, 'u'))\n")
    assert len(_orphans(src)) == 1


def test_aliased_requests_module_is_orphan():
    """A requests module alias cannot bypass the span-root gate."""
    src = "import requests as client\ndef f():\n    client.post('u')\n"
    assert len(_orphans(src)) == 1


def test_imported_requests_verb_is_orphan():
    """A verb imported directly from requests remains a transport call."""
    src = "from requests import delete as remove\ndef f():\n    remove('u')\n"
    assert len(_orphans(src)) == 1


def test_assigned_requests_module_alias_is_orphan():
    """A second-level module alias remains bound to requests."""
    src = ("import requests\ndef f():\n"
           "    transport = requests\n"
           "    transport.put('u')\n")
    assert len(_orphans(src)) == 1


def test_requests_head_and_options_are_orphans():
    """Less common requests verbs receive the same tracing enforcement."""
    src = ("import requests\ndef f():\n"
           "    requests.head('u')\n"
           "    requests.options('u')\n")
    assert len(_orphans(src)) == 2


def test_constructed_requests_session_call_is_orphan():
    """A requests Session method cannot bypass tracing through indirection."""
    src = ("import requests\ndef f():\n"
           "    session = requests.Session()\n"
           "    session.patch('u')\n")
    assert len(_orphans(src)) == 1


def test_aliased_httpx_client_call_is_orphan():
    """An aliased httpx Client method is an outbound HTTP call."""
    src = ("import httpx as hx\ndef f():\n"
           "    client = hx.Client()\n"
           "    client.put('u')\n")
    assert len(_orphans(src)) == 1


def test_urllib_urlopen_alias_is_orphan():
    """An imported urllib urlopen alias remains an outbound HTTP call."""
    src = ("from urllib.request import urlopen as open_url\ndef f():\n"
           "    open_url('u')\n")
    assert len(_orphans(src)) == 1


def test_annotated_session_parameter_is_orphan():
    """A passed Session remains visible when its parameter is annotated."""
    src = ("import requests\ndef f(session: requests.Session):\n"
           "    session.post('u')\n")
    assert len(_orphans(src)) == 1


def test_http_client_connection_request_is_orphan():
    """The stdlib HTTPConnection request method is transport I/O."""
    src = ("import http.client as hc\ndef f():\n"
           "    connection = hc.HTTPSConnection('host')\n"
           "    connection.request('GET', '/')\n")
    assert len(_orphans(src)) == 1


def test_urllib3_pool_request_is_orphan():
    """A urllib3 PoolManager alias cannot bypass tracing."""
    src = ("from urllib3 import PoolManager as Pool\ndef f():\n"
           "    pool = Pool()\n"
           "    pool.request('GET', 'u')\n")
    assert len(_orphans(src)) == 1


def test_aiohttp_session_request_is_orphan():
    """An aiohttp async context-manager binding is resolved."""
    src = ("import aiohttp as net\nasync def f():\n"
           "    async with net.ClientSession() as session:\n"
           "        await session.get('u')\n")
    assert len(_orphans(src)) == 1


def test_aliased_transport_inside_client_span_is_traced():
    """Alias resolution does not flag a call already under client_span."""
    src = ("import requests as client\ndef f():\n"
           "    with tracing.client_span('u', 'POST'):\n"
           "        client.post('u')\n")
    assert _orphans(src) == []


def test_non_transport_get_method_is_ignored():
    """Generic objects using a get method are outside the HTTP signature set."""
    assert _orphans("def f(mapping):\n    return mapping.get('key')\n") == []


def test_comments_and_docstrings_are_ignored():
    """Text that resembles a transport call is not executable AST."""
    src = ('"""requests.post(\\"u\\")"""\n'
           "# httpx.get('u')\n"
           "def f():\n    return None\n")
    assert _orphans(src) == []


def test_function_local_aliases_do_not_overwrite_other_scopes():
    """A non-HTTP alias in one function cannot hide another scope's call."""
    src = (
        "def request_data():\n"
        "    import requests as client\n"
        "    client.get('u')\n"
        "def decode_data():\n"
        "    import json as client\n"
        "    return client.loads('{}')\n"
    )
    assert len(_orphans(src)) == 1


def test_reused_client_names_remain_local_and_converge_once():
    """Independent client bindings remain visible without global rescans."""
    src = (
        "def sync_request():\n"
        "    import requests\n"
        "    client = requests.Session()\n"
        "    client.get('u')\n"
        "def async_request():\n"
        "    import httpx\n"
        "    client = httpx.AsyncClient()\n"
        "    client.get('u')\n"
    )
    assert len(_orphans(src)) == 2


def test_wrapper_covers_only_the_callable_it_receives():
    """An unrelated raw call remains orphaned in a wrapper-using function."""
    src = (
        "import functools, requests\n"
        "def f():\n"
        "    call = functools.partial(requests.get, 'u')\n"
        "    tracing.traced_request('u', 'GET', call)\n"
        "    requests.post('u')\n"
    )
    assert len(_orphans(src)) == 1


def test_real_repo_gate_is_clean():
    """The shipped baseline covers the real repo — main() returns 0."""
    assert gate.main() == 0
