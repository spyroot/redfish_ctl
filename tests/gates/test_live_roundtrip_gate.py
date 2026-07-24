"""Offline tests for the live round-trip helper and its enforcement gate.

The helper (tests/live_utils.py) must prove capture -> set -> assert ->
restore -> assert, restoring even when the middle assertion fails. The gate
(tools/live_mutation_gate.py) must reject a live test that PATCHes directly
and accept one that goes through the helper. All offline: the BMC is a fake.

Author Mus spyroot@gmail.com
"""
import textwrap

import pytest

from tests.live_utils import RoundTripError, live_roundtrip
from tools.live_mutation_gate import find_violations, main


class _FakeBmc:
    """In-memory stand-in for a manager: one resource dict, call recording.

    :param initial: starting resource JSON.
    :param fail_reads_after: raise on reads after this many (simulates loss).
    :param drop_writes: when True, PATCHes are accepted but change nothing —
        the shape of a BMC that answers 2xx yet ignores the attribute.
    """

    def __init__(self, initial: dict, drop_writes: bool = False):
        self.data = dict(initial)
        self.drop_writes = drop_writes
        self.patches: list[dict] = []

    def base_query(self, resource):
        """Return the current resource state like base_query's CommandResult.

        :param resource: resource path (ignored; one-resource fake).
        :return: object with a .data dict.
        """
        class _R:
            pass
        r = _R()
        r.data = dict(self.data)
        return r

    def base_patch(self, resource, payload=None, **kwargs):
        """Record and apply (or drop) a PATCH.

        :param resource: resource path (ignored; one-resource fake).
        :param payload: attribute dict to apply.
        :param kwargs: accepted for CLI compatibility; not used.
        :return: None — the helper judges by read-back, not by return.
        """
        self.patches.append(dict(payload or {}))
        if not self.drop_writes:
            self.data.update(payload or {})


def test_roundtrip_happy_path_restores_and_records_both_patches():
    """A clean round-trip writes new then pre, leaving the BMC unchanged."""
    bmc = _FakeBmc({"AssetTag": "orig"})
    live_roundtrip(bmc, "/redfish/v1/Systems/1", "AssetTag", "probe")
    assert bmc.data["AssetTag"] == "orig"
    assert bmc.patches == [{"AssetTag": "probe"}, {"AssetTag": "orig"}]


def test_roundtrip_write_ignored_still_restores_and_fails():
    """A BMC that accepts-but-ignores the write fails the set assertion, and
    the restore PATCH still runs — a red test may not leave state behind."""
    bmc = _FakeBmc({"AssetTag": "orig"}, drop_writes=True)
    with pytest.raises(RoundTripError, match="read back"):
        live_roundtrip(bmc, "/redfish/v1/Systems/1", "AssetTag", "probe")
    assert len(bmc.patches) == 2, "restore must run even on failure"
    assert bmc.patches[-1] == {"AssetTag": "orig"}


def test_roundtrip_restore_mismatch_is_loud():
    """When the restore read-back mismatches, the error says the BMC was left
    modified — the one outcome that must never be silent."""
    class _StickyBmc(_FakeBmc):
        def base_patch(self, resource, payload=None, **kwargs):
            self.patches.append(dict(payload or {}))
            if len(self.patches) == 1:          # first write lands...
                self.data.update(payload or {})  # ...restore is ignored

    bmc = _StickyBmc({"AssetTag": "orig"})
    with pytest.raises(RoundTripError, match="LEFT MODIFIED"):
        live_roundtrip(bmc, "/redfish/v1/Systems/1", "AssetTag", "probe")


def test_roundtrip_restores_when_first_patch_lands_then_raises():
    """The safety-critical case: a PATCH reaches the BMC and is applied, then
    raises on the response read (timeout/reset). Restore MUST still run, or the
    BMC is left modified. The original error propagates; the value is back."""
    class _ApplyThenRaiseBmc(_FakeBmc):
        def base_patch(self, resource, payload=None, **kwargs):
            self.patches.append(dict(payload or {}))
            self.data.update(payload or {})       # BMC applied the change...
            if len(self.patches) == 1:
                raise RuntimeError("timeout reading response after apply")

    bmc = _ApplyThenRaiseBmc({"AssetTag": "orig"})
    with pytest.raises(RuntimeError, match="timeout"):
        live_roundtrip(bmc, "/redfish/v1/Systems/1", "AssetTag", "probe")
    assert len(bmc.patches) == 2, "restore must run after a land-then-raise PATCH"
    assert bmc.data["AssetTag"] == "orig", "BMC must be restored"


def test_roundtrip_no_body_raises():
    """A resource read that yields no JSON body cannot be round-tripped."""
    class _EmptyBmc(_FakeBmc):
        def base_query(self, resource):
            class _R:
                data = None
            return _R()

    with pytest.raises(RoundTripError, match="no JSON body"):
        live_roundtrip(_EmptyBmc({}), "/redfish/v1/Systems/1", "AssetTag", "x")


def _write(tmp_path, name, body):
    """Write one test module into a scratch tests tree.

    :param tmp_path: pytest tmp_path fixture value.
    :param name: file name to create.
    :param body: module source.
    :return: the created path.
    """
    p = tmp_path / name
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def test_gate_flags_direct_patch_in_live_test(tmp_path):
    """A live-marked module calling base_patch directly is a violation."""
    p = _write(tmp_path, "test_x.py", """
        import pytest
        pytestmark = pytest.mark.live
        def test_bad(mgr):
            mgr.base_patch("/redfish/v1/Systems/1", payload={"A": 1})
        """)
    hits = find_violations(p)
    assert [name for _, name in hits] == ["base_patch"]


def test_gate_ignores_docstring_and_comment_mentions(tmp_path):
    """Mentions of base_patch in docstrings/comments must not trip the gate —
    the AST sees calls, not prose. (The regex guard in PR #274 failed here.)"""
    p = _write(tmp_path, "test_x.py", """
        import pytest
        pytestmark = pytest.mark.live
        def test_ok():
            '''This test never calls base_patch( directly.'''
            # base_patch("/x", payload={}) would be a violation
            assert True
        """)
    assert find_violations(p) == []


def test_gate_ignores_non_live_modules(tmp_path):
    """Offline tests may call the primitives freely — only live is gated."""
    p = _write(tmp_path, "test_x.py", """
        def test_offline(mgr):
            mgr.base_patch("/redfish/v1/Systems/1", payload={"A": 1})
        """)
    assert find_violations(p) == []


def test_gate_accepts_helper_usage(tmp_path):
    """The sanctioned form — live test via live_roundtrip — is clean."""
    p = _write(tmp_path, "test_x.py", """
        import pytest
        from tests.live_utils import live_roundtrip
        pytestmark = pytest.mark.live
        def test_ok(mgr):
            live_roundtrip(mgr, "/redfish/v1/Systems/1", "AssetTag", "probe")
        """)
    assert find_violations(p) == []


def test_gate_scans_a_live_file_named_live_utils(tmp_path):
    """A live test named live_utils.py must still be scanned — the helper is
    exempt because it carries no live marker, not because of its name."""
    p = _write(tmp_path, "live_utils.py", """
        import pytest
        pytestmark = pytest.mark.live
        def test_sneaky(mgr):
            mgr.base_patch("/redfish/v1/Systems/1", payload={"A": 1})
        """)
    assert [name for _, name in find_violations(p)] == ["base_patch"]


def test_gate_main_exit_codes(tmp_path):
    """main() exits 1 on a violation tree and 0 on a clean tree."""
    _write(tmp_path, "test_bad.py", """
        import pytest
        pytestmark = pytest.mark.live
        def test_bad(mgr):
            mgr.invoke_action("/redfish/v1/x", payload={})
        """)
    assert main(["--tests-dir", str(tmp_path)]) == 1
    (tmp_path / "test_bad.py").unlink()
    assert main(["--tests-dir", str(tmp_path)]) == 0
