"""Offline tests for the span-root ratchet gate.

The gate (tools/span_root_gate.py) flags HTTP module aliases, imported verbs,
and Session/client methods that run outside a CLIENT span and are not handed to
a tracing wrapper. It must recognize all three real tracing patterns (with
client_span; traced_request; and the async traced_request_callable) and flag
only genuine orphans. Driven by parsing small source snippets, so it tests the
AST logic directly.

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


def test_session_get_is_orphan():
    """A requests Session GET must not bypass the raw-HTTP ratchet."""
    src = "import requests\ndef f():\n    session = requests.Session()\n    session.get('u')\n"
    assert _orphans(src) == ["<mod>:4"]


def test_session_get_inside_client_span_is_traced():
    """A Session GET is accepted only inside the CLIENT-span boundary."""
    src = (
        "import requests\ndef f():\n"
        "    session = requests.Session()\n"
        "    with tracing.client_span('u', 'GET'):\n"
        "        session.get('u')\n"
    )
    assert _orphans(src) == []


def test_mapping_get_is_not_an_http_call():
    """Ordinary mapping access must not be mistaken for a Session request."""
    assert _orphans("def f(data):\n    return data.get('key')\n") == []


def test_wrapper_elsewhere_does_not_cover_unrelated_raw_call():
    """A wrapper call cannot bless every raw request in the same function."""
    src = (
        "import requests\ndef f():\n"
        "    tracing.traced_request('u', 'GET', lambda: None)\n"
        "    requests.get('other')\n"
    )
    assert _orphans(src) == ["<mod>:4"]


def test_operation_span_does_not_replace_client_span():
    """An INTERNAL operation span is insufficient coverage for raw HTTP."""
    src = (
        "import requests\ndef f():\n"
        "    with tracing.operation_span('command'):\n"
        "        requests.get('u')\n"
    )
    assert _orphans(src) == ["<mod>:4"]


def test_requests_module_alias_is_checked():
    """Aliasing the requests module cannot evade the raw-HTTP gate."""
    src = "import requests as http\ndef f():\n    http.post('u')\n"
    assert _orphans(src) == ["<mod>:3"]


def test_requests_function_alias_is_checked():
    """An imported request function remains subject to the gate."""
    src = "from requests import get as fetch\ndef f():\n    fetch('u')\n"
    assert _orphans(src) == ["<mod>:3"]


def test_other_http_library_alias_is_checked():
    """A supported HTTP client module cannot bypass the gate by substitution."""
    src = "import httpx as http\ndef f():\n    http.get('u')\n"
    assert _orphans(src) == ["<mod>:3"]


def test_real_repo_gate_is_clean():
    """The shipped baseline covers the real repo — main() returns 0."""
    assert gate.main() == 0
