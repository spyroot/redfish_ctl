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

# Put the tests/ dir itself on sys.path so bare sibling imports used across the
# suite (``from vendor_corpus import ...``, ``from conftest import ...``) resolve
# from ANY subdirectory. This is what lets tests live in mirrored domain dirs
# (tests/<domain>/test_<verb>.py) without rewriting their imports: the root
# conftest runs before any subdir test module is imported, so tests/ is already
# on the path when a nested test does a bare sibling import.
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR in sys.path:
    sys.path.remove(_TESTS_DIR)
sys.path.insert(0, _TESTS_DIR)

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
    from redfish_ctl.idrac_shared import Singleton
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


# Vendor-faithful task realization. Only Dell's OEM job service returns a ``JID_``
# id (the literal the Dell-specific ``re.search("JID_")`` body scrape in
# redfish_manager.job_id_from_respond matches); every other vendor exposes the
# generic DMTF TaskService, whose monitor id is a plain token, never a ``JID_``.
# Serving a ``JID_`` for a non-Dell box is exactly the fabrication that makes a
# command — or an agent reading the response — wrongly conclude the vendor
# realizes jobs the Dell way. The mock is faithful to the task *structure*
# (Dell OEM job vs DMTF TaskService); whether a POST realizes synchronously (204)
# or as a 202 task is a live-capture detail the read-only corpus cannot prove,
# and stays marked unobserved in the per_api_map spec, not invented here.
_DELL_JOB_ID = "JID_000000000001"      # Dell OEM DellJobService job id
_DMTF_TASK_ID = "1"                     # DMTF TaskService monitor id (all non-Dell)


def _vendor_family(vendor):
    """Collapse a fixture-set name to its vendor family.

    ``supermicro_x10_119`` -> ``supermicro``; ``generic``/``dmtf`` -> ``generic``.

    :param vendor: the fixture-set name passed to ``redfish_mock_factory``.
    :return: the vendor family that selects the task-realization shape.
    """
    v = (vendor or "dell").lower()
    for fam in ("supermicro", "dell", "hpe", "lenovo"):
        if v.startswith(fam):
            return fam
    return "generic"


def _vendor_task_id(vendor):
    """The task id a vendor returns for an Action POST that realizes as a task.

    Dell returns its OEM ``JID_`` job; every other vendor returns a DMTF
    TaskService monitor id. Preserving this difference is what lets a command (or
    an agent) SEE that ``JID_`` is a Dell literal, not a cross-vendor contract.

    :param vendor: the fixture-set name passed to ``redfish_mock_factory``.
    :return: the vendor-faithful task id string.
    """
    return _DELL_JOB_ID if _vendor_family(vendor) == "dell" else _DMTF_TASK_ID


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
    * POST   -> protocol-accurate per shape: ``SubmitTestEvent`` -> 204 (sync);
                subscription create (``/Subscriptions``) -> 201 + ``Location``;
                other ``/Actions/`` -> 202 with a ``Location`` task header; else 204.
                The 202 task id is VENDOR-FAITHFUL: ``vendor="dell"`` returns an OEM
                ``JID_`` job; every other vendor a plain DMTF TaskService id (never
                ``JID_``). See ``_vendor_task_id``.
    * DELETE -> 200

    ``requests`` records every call, so tests can inspect ``service.last_request``.
    """

    # Class default = Dell (the bare ``redfish_service`` fixture and legacy tests
    # that read ``MockRedfishService.JOB_ID``). The per-instance ``self.JOB_ID``
    # set below is what tests actually assert against and moves with the vendor.
    JOB_ID = _DELL_JOB_ID

    def __init__(self, fixture_dir: Path, index=None, vendor: str = "dell"):
        """Build a mock Redfish service for one vendor's fixture tree.

        :param fixture_dir: directory of flattened ``_redfish_v1_*.json`` GET fixtures.
        :param index: optional prebuilt case-insensitive fixture index; the
            module default is used when omitted.
        :param vendor: fixture-set name (e.g. ``dell``, ``supermicro``,
            ``supermicro_x10_119``, ``generic``, ``hpe``); selects the
            vendor-faithful task-realization shape via ``_vendor_task_id``.
        """
        self._dir = fixture_dir
        self._index = index if index is not None else _FIXTURE_INDEX
        self._overlay = {}  # path -> materialized state dict
        self.requests = []
        self.vendor = vendor
        # Vendor-faithful task id: Dell -> JID_ OEM job; all others -> DMTF id.
        self.JOB_ID = _vendor_task_id(vendor)

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
        # A settable-resource PATCH (e.g. a Bios @Redfish.Settings object) stages
        # the change as pending and applies it per @Redfish.SettingsApplyTime — it
        # is DEFERRED, so it returns 200 with a completion message and NEVER a
        # TaskService monitor. That keeps the realization unambiguous
        # (task_id_shape=none) for a deferred-settings write.
        context.status_code = 200
        return json.dumps(
            {"@Message.ExtendedInfo": [{"MessageId": "Base.1.12.Success",
                                        "Message": "Successfully Completed Request",
                                        "Severity": "OK"}]}
        )

    def post_cb(self, request, context):
        self.requests.append(request)
        path = request.path.lower()
        # EventService.SubmitTestEvent is a SYNCHRONOUS DMTF action: 204, no task.
        # A blanket 202+task here made a lens read the op as async — it is not.
        if "submittestevent" in path:
            context.status_code = 204
            return ""
        # Creating an EventDestination (subscription) is a resource CREATE, not an
        # action: DMTF returns 201 + a Location at the new subscription, never a
        # task. (The POST lands on the collection, so it has no /actions/ segment.)
        if path.rstrip("/").endswith("/subscriptions"):
            context.status_code = 201
            context.headers["Location"] = request.path.rstrip("/") + "/1"
            return ""
        if "/actions/" in path:
            # A vendor that realizes an Action as a task returns 202 + a Location
            # header at the new task. The id is vendor-faithful (Dell JID_ OEM job
            # vs DMTF TaskService id) so a JID_ scrape against a non-Dell response
            # correctly finds nothing.
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
    from redfish_ctl.idrac_manager import IDracManager
    return IDracManager(
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
    from redfish_ctl.idrac_shared import Singleton
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
    """An IDracManager wired to the mocked Redfish service (offline, no hardware).

    Backed by the captured DMTF mockup tree; exercises the real ``requests`` code
    path. Requires the ``requests-mock`` dev dependency; skips cleanly without it.
    """
    yield _make_idrac("mock-idrac", "root", "mock")


@pytest.fixture
def redfish_mock_factory():
    """Factory for a VENDOR-shaped offline IDracManager.

    ``mgr, svc = factory("supermicro")`` serves the DMTF base overlaid by
    ``tests/supermicro_fixtures/`` (NOT the Dell ``idrac_fixtures/``), so the same
    command/transport code runs against a real non-Dell tree (System_0/BMC_0)
    instead of System.Embedded.1. The service is also vendor-faithful on writes:
    a Supermicro/generic/HPE Action returns a DMTF TaskService id, only a Dell
    tree returns a ``JID_`` job. Returns ``(IDracManager, MockRedfishService)``.
    """
    requests_mock = pytest.importorskip("requests_mock")
    _started = []

    def _factory(vendor):
        index = _build_fixture_index(_FIXTURE_DIR, _vendor_fixture_dir(vendor))
        service = MockRedfishService(_FIXTURE_DIR, index=index, vendor=vendor)
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
