"""Shared pytest fixtures and collection rules for redfish_ctl tests.

The default unit suite runs fully offline. Tests that talk to a real iDRAC
must be marked ``@pytest.mark.live``; those are skipped automatically unless
``IDRAC_IP`` is present in the environment, so ``pytest`` is green on a laptop
or in CI without any hardware.

Import-path note: the repo root directory is itself named ``redfish_ctl`` and
ships a re-export shim (``./__init__.py`` does ``from .redfish_ctl import *``).
If the repo's *parent* directory ends up on ``sys.path`` first, ``import
redfish_ctl`` resolves to that shim instead of the real nested package, and
submodules like ``redfish_ctl.cmd_utils`` become unreachable. We pin the source
tree as the first entry and drop the parent so the nested package always wins.

Author Mus spyroot@gmail.com
"""
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PARENT = os.path.dirname(_REPO_ROOT)

# Captured DMTF Redfish mockup tree shipped in the package. Filenames map 1:1 to
# Redfish URLs: /redfish/v1/Managers -> _redfish_v1_Managers.json
_FIXTURE_DIR = Path(_REPO_ROOT) / "redfish_ctl" / "json_responses"

# Drop the parent dir so the repo-root re-export shim cannot shadow the real
# nested package under the bare name ``redfish_ctl``.
while _PARENT in sys.path:
    sys.path.remove(_PARENT)
# Search the source tree first.
if _REPO_ROOT in sys.path:
    sys.path.remove(_REPO_ROOT)
sys.path.insert(0, _REPO_ROOT)

# Eagerly bind the bare name ``redfish_ctl`` to the real nested package and cache
# it in sys.modules now, while the parent dir is off the path. pytest may re-add
# the parent during collection, but a cached module wins over any later lookup,
# so lazy imports inside fixtures/tests cannot resolve the repo-root shim.
import importlib.util  # noqa: E402

_nested_init = os.path.join(_REPO_ROOT, "redfish_ctl", "__init__.py")
try:
    _pkg = importlib.import_module("redfish_ctl")
    _correct = getattr(_pkg, "__file__", "") == _nested_init
except Exception:
    # e.g. a sibling repo dir named redfish_ctl (a git worktree next to the repo)
    # or the repo-root re-export shim raising on its relative import.
    _correct = False
    _pkg = None
if not _correct:
    # Wrong/failed import: force-load THIS tree's nested package by path.
    sys.modules.pop("redfish_ctl", None)
    spec = importlib.util.spec_from_file_location(
        "redfish_ctl", _nested_init,
        submodule_search_locations=[os.path.dirname(_nested_init)],
    )
    _pkg = importlib.util.module_from_spec(spec)
    sys.modules["redfish_ctl"] = _pkg
    spec.loader.exec_module(_pkg)


def _has_live_idrac() -> bool:
    """True when an iDRAC endpoint is configured via the environment."""
    return bool(os.environ.get("IDRAC_IP", "").strip())


@pytest.fixture(autouse=True)
def _reset_command_singletons():
    """Drop cached command-singleton instances between tests.

    redfish_ctl commands use the ``Singleton`` metaclass, and instances memoize
    per-box state via ``cached_property`` (notably ``idrac_manage_servers``). Left
    alone, the first vendor a command runs against would freeze that state for the
    whole session, so a Dell test and a Supermicro test sharing a command class
    (e.g. ``reboot``) would poison each other depending on collection order. The
    command ``_registry`` (used for dispatch) is a separate dict and is untouched.
    """
    from redfish_ctl.redfish_manager_shared import Singleton
    Singleton._instances.clear()
    yield
    Singleton._instances.clear()


def pytest_collection_modifyitems(config, items):
    """Skip ``live`` tests when no iDRAC endpoint is configured."""
    if _has_live_idrac():
        return
    skip_live = pytest.mark.skip(reason="no IDRAC_IP set; skipping live iDRAC test")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


# Hand-authored iDRAC-shaped fixtures (Dell paths like System.Embedded.1) that the
# generic DMTF capture does not contain. These overlay the captured tree so
# command-level tests can run offline.
_IDRAC_FIXTURE_DIR = Path(os.path.dirname(os.path.abspath(__file__))) / "idrac_fixtures"

# Case-insensitive index of the fixture tree. requests-mock lowercases
# request.path, and Redfish paths are mixed-case (e.g. /redfish/v1/Managers), so
# we must match without relying on a case-insensitive filesystem (macOS hides the
# bug; Linux/CI would not). idrac_fixtures/ wins over the captured DMTF tree.
_FIXTURE_INDEX = {}
for _dir in (_FIXTURE_DIR, _IDRAC_FIXTURE_DIR):
    if _dir.exists():
        for _f in _dir.glob("*.json"):
            _FIXTURE_INDEX[_f.name.lower()] = _f


def _build_fixture_index(*dirs):
    """Case-insensitive {flattened-filename: path} index; later dirs win."""
    index = {}
    for _d in dirs:
        if _d and _d.exists():
            for _f in _d.glob("*.json"):
                index[_f.name.lower()] = _f
    return index


def _vendor_fixture_dir(vendor):
    """tests/<vendor>_fixtures/ — a vendor overlay on the DMTF base (NOT the Dell overlay)."""
    return Path(os.path.dirname(os.path.abspath(__file__))) / f"{vendor}_fixtures"


def _url_to_fixture(path: str, index=None):
    """Map a Redfish request path to its captured mockup file, case-insensitively.

    ``/redfish/v1/Managers`` -> ``_redfish_v1_Managers.json``. Returns ``None``
    when no fixture exists.
    """
    key = "_" + path.strip("/").replace("/", "_") + ".json"
    return (index if index is not None else _FIXTURE_INDEX).get(key.lower())


class MockRedfishService:
    """A small stateful Redfish service backed by the captured DMTF mockup tree.

    Serves GET from ``redfish_ctl/json_responses`` (with an in-memory overlay so a
    PATCH is visible to a later GET), and gives plausible spec-shaped answers for
    the mutating verbs so command tests can assert the request the client *sends*:

    * GET    -> fixture JSON (200) or 404 when no fixture exists
    * PATCH  -> deep-merges the body into state, 200 + a success message
    * POST   -> 202 with a ``Location`` task header for ``/Actions/`` calls
                (mirrors how iDRAC returns a JID job), else 204
    * DELETE -> 200

    ``requests`` records every call, so tests can inspect ``service.last_request``.
    """

    JOB_ID = "JID_000000000001"

    def __init__(self, fixture_dir: Path, index=None):
        self._dir = fixture_dir
        self._index = index if index is not None else _FIXTURE_INDEX
        self._overlay = {}  # path -> materialized state dict
        self.requests = []

    def _state(self, path: str):
        if path in self._overlay:
            return self._overlay[path]
        fixture = _url_to_fixture(path, self._index)
        if fixture is None:
            return None
        import json
        return json.loads(fixture.read_text())

    @staticmethod
    def _deep_merge(base: dict, patch: dict) -> dict:
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                MockRedfishService._deep_merge(base[key], value)
            else:
                base[key] = value
        return base

    def get_cb(self, request, context):
        import json
        self.requests.append(request)
        state = self._state(request.path)
        if state is None:
            context.status_code = 404
            return json.dumps({"error": f"no fixture for {request.path}"})
        context.status_code = 200
        return json.dumps(state)

    def patch_cb(self, request, context):
        import json
        self.requests.append(request)
        state = self._state(request.path)
        if state is None:
            state = {}
        body = request.json() if request.text else {}
        self._overlay[request.path] = self._deep_merge(state, body)
        context.status_code = 200
        return json.dumps(
            {"@Message.ExtendedInfo": [{"MessageId": "Base.1.12.Success",
                                        "Message": "Successfully Completed Request",
                                        "Severity": "OK"}]}
        )

    def post_cb(self, request, context):
        self.requests.append(request)
        if "/actions/" in request.path.lower():
            # iDRAC returns 202 + a Location header pointing at the new job.
            context.status_code = 202
            context.headers["Location"] = (
                f"/redfish/v1/TaskService/Tasks/{self.JOB_ID}"
            )
            return ""
        context.status_code = 204
        return ""

    def delete_cb(self, request, context):
        self.requests.append(request)
        context.status_code = 200
        return ""

    @property
    def last_request(self):
        return self.requests[-1] if self.requests else None


def _make_idrac(idrac_ip, username, password):
    from redfish_ctl.redfish_manager_base import RedfishManagerBase
    return RedfishManagerBase(
        idrac_ip=idrac_ip,
        idrac_username=username,
        idrac_password=password,
        insecure=True,
        is_debug=False,
    )


@pytest.fixture(autouse=True)
def _reset_command_singletons():
    """Give every test fresh command singletons so no cached state leaks.

    Commands use ``metaclass=Singleton`` and cache per-host state
    (``idrac_manage_servers`` and friends) on the instance. Without a reset, the
    first vendor a command sees wins for the whole session — which only bites
    cross-vendor tests (e.g. Supermicro then HPE resolve different host ids).
    Clearing the instance registry before each test isolates them.
    """
    from redfish_ctl.redfish_manager_shared import Singleton
    Singleton._instances.clear()
    yield
    Singleton._instances.clear()


@pytest.fixture
def redfish_service():
    """The bare MockRedfishService mounted on a ``requests-mock`` transport.

    Use when a test needs to inspect the captured requests (``service.last_request``)
    or pre-seed state. Most tests can use ``redfish_mock`` / ``redfish_api`` instead.
    """
    requests_mock = pytest.importorskip("requests_mock")
    service = MockRedfishService(_FIXTURE_DIR)
    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=service.get_cb)
        mocker.patch(requests_mock.ANY, text=service.patch_cb)
        mocker.post(requests_mock.ANY, text=service.post_cb)
        mocker.delete(requests_mock.ANY, text=service.delete_cb)
        service.mocker = mocker
        yield service


@pytest.fixture
def redfish_mock(redfish_service):
    """An RedfishManagerBase wired to the mocked Redfish service (offline, no hardware).

    Backed by the captured DMTF mockup tree; exercises the real ``requests`` code
    path. Requires the ``requests-mock`` dev dependency; skips cleanly without it.
    """
    yield _make_idrac("mock-idrac", "root", "mock")


@pytest.fixture
def redfish_mock_factory():
    """Factory for a VENDOR-shaped offline RedfishManagerBase.

    ``mgr, svc = factory("supermicro")`` serves the DMTF base overlaid by
    ``tests/supermicro_fixtures/`` (NOT the Dell ``idrac_fixtures/``), so the same
    command/transport code runs against a real non-Dell tree (System_0/BMC_0)
    instead of System.Embedded.1. Returns ``(RedfishManagerBase, MockRedfishService)``.
    """
    requests_mock = pytest.importorskip("requests_mock")
    _started = []

    def _factory(vendor):
        index = _build_fixture_index(_FIXTURE_DIR, _vendor_fixture_dir(vendor))
        service = MockRedfishService(_FIXTURE_DIR, index=index)
        mocker = requests_mock.Mocker()
        mocker.start()
        mocker.get(requests_mock.ANY, text=service.get_cb)
        mocker.patch(requests_mock.ANY, text=service.patch_cb)
        mocker.post(requests_mock.ANY, text=service.post_cb)
        mocker.delete(requests_mock.ANY, text=service.delete_cb)
        service.mocker = mocker
        _started.append(mocker)
        return _make_idrac(f"mock-{vendor}", "root", "mock"), service

    yield _factory
    for _m in _started:
        _m.stop()


@pytest.fixture
def redfish_api(request):
    """Dual-mode iDRAC client: **live** when ``IDRAC_IP`` is set, else **mock**.

    Write a command/transport test once against this fixture and it runs offline
    by default (mock mode) and against real hardware when ``IDRAC_IP`` is exported
    (live mode). A test that mutates state should also carry ``@pytest.mark.live``
    so it only runs against an approved iDRAC, never just because IDRAC_IP is set.
    """
    if _has_live_idrac():
        yield _make_idrac(
            os.environ["IDRAC_IP"],
            os.environ.get("IDRAC_USERNAME", "root"),
            os.environ.get("IDRAC_PASSWORD", ""),
        )
    else:
        # Reuse the mock service fixture so mock mode is fully offline.
        service = request.getfixturevalue("redfish_service")
        yield _make_idrac("mock-idrac", "root", "mock")
        _ = service  # keep the mock mounted for the test duration
