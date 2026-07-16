"""Structural conventions enforced over the package source.

These tests parse every module under ``redfish_ctl/`` into an AST and fail
on code shapes that are known bugs, so the class of mistake cannot be
reintroduced anywhere — including in files that do not exist yet.

Rule 1 — no enum-member truthiness on API respond values.
``base_post``/``base_patch``/``base_delete`` return an ``RedfishApiRespond``
enum member. Testing it with attribute access (``api_resp.Success``)
fetches the always-truthy class member instead of comparing, so an Error
response satisfied success branches; three commands escalated a FAILED
write into a commit with reboot before this was fixed. The only valid
member references are through the enum class itself
(``RedfishApiRespond.Success``); comparisons must use ``==``/``in``.

Rule 2 — new direct mutation calls must carry a guard argument (see
``test_new_direct_mutation_calls_are_guarded_or_baselined`` below).

Rule 3 — no ``return``/``break``/``continue`` escaping a ``finally`` block.
Such a statement silently swallows any in-flight exception (and any earlier
``return``), and Python 3.14 emits ``SyntaxWarning`` for it (PEP 765).
``parse_json_respond_msg`` shipped with ``finally: return redfish_resp``,
which hid every unexpected parse failure. Statements bound inside the
``finally`` itself — a ``return`` in a nested ``def``, or a ``break``/
``continue`` targeting a loop that starts inside the ``finally`` — are fine.

Author Mus spyroot@gmail.com
"""
import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "redfish_ctl"

RESPOND_MEMBERS = {"Success", "Ok", "AcceptedTaskGenerated", "Error"}
MUTATING_HELPERS = {"base_post", "base_patch", "base_delete"}
MUTATION_GUARD_ARGS = {"dry_run", "confirm", "confirm_irreversible", "acct_confirm"}

# Existing direct write sites that predate the guarded-command convention.
# New command code should expose one of MUTATION_GUARD_ARGS or route through
# invoke_action instead of extending this list.
LEGACY_UNGUARDED_MUTATION_CALLS = {
    "redfish_ctl/attribute/cmd_attribute_clear_pending.py:94 execute base_post",
    "redfish_ctl/attribute/cmd_attribute_update.py:95 execute base_patch",
    "redfish_ctl/bios/cmd_bios_clear_pending.py:81 execute base_post",
    "redfish_ctl/bios/cmd_change_bios.py:369 execute base_patch",
    "redfish_ctl/bios/cmd_change_boot_order.py:177 execute base_patch",
    "redfish_ctl/boot_source/cmd_clear_pending.py:74 execute base_post",
    "redfish_ctl/boot_source/cmd_enable.py:121 execute base_patch",
    "redfish_ctl/boot_source/cmd_update.py:175 execute base_patch",
    "redfish_ctl/chassis/cmd_chasis_reset.py:115 execute base_post",
    "redfish_ctl/chassis/cmd_update_chassis.py:119 execute base_patch",
    "redfish_ctl/dell_lc/cmd_dell_lc_api.py:57 execute base_post",
    "redfish_ctl/dell_lc/cmd_dell_lc_rs.py:56 execute base_post",
    "redfish_ctl/delloem/delloem_attach.py:91 execute base_post",
    "redfish_ctl/delloem/delloem_attach_status.py:67 execute base_post",
    "redfish_ctl/delloem/delloem_boot_netios.py:98 execute base_post",
    "redfish_ctl/delloem/delloem_detach.py:57 execute base_post",
    "redfish_ctl/delloem/delloem_disconnect.py:56 execute base_post",
    "redfish_ctl/delloem/delloem_get_networkios.py:66 execute base_post",
    "redfish_ctl/jobs/cmd_job_apply.py:149 execute base_post",
    "redfish_ctl/jobs/cmd_job_del.py:66 execute base_delete",
    "redfish_ctl/jobs/cmd_job_delete_all.py:83 execute base_post",
    "redfish_ctl/manager/cmd_manager_reset.py:99 execute base_post",
    "redfish_ctl/manager/cmd_manager_time.py:124 execute base_patch",
    "redfish_ctl/storage/cmd_convert_none_raid.py:115 execute base_post",
    "redfish_ctl/storage/cmd_convert_to_raid.py:117 execute base_post",
    "redfish_ctl/system/cmd_system_config.py:119 execute base_post",
    "redfish_ctl/system/cmd_system_import.py:152 execute base_post",
    "redfish_ctl/virtual_media/cmd_smc_virtual_media.py:138 execute base_post",
    "redfish_ctl/virtual_media/cmd_smc_virtual_media.py:151 execute base_patch",
    "redfish_ctl/virtual_media/cmd_smc_virtual_media.py:158 execute base_post",
    "redfish_ctl/virtual_media/cmd_virtual_media_eject.py:99 execute base_post",
    "redfish_ctl/virtual_media/cmd_virtual_media_insert.py:208 execute base_post",
    "redfish_ctl/volumes/cmd_initilize.py:81 execute base_post",
}


def _accessed_on(node: ast.Attribute) -> str | None:
    """Name the object a member is fetched from: ``x.Success`` -> ``x``."""
    value = node.value
    if isinstance(value, ast.Name):
        return value.id
    if isinstance(value, ast.Attribute):
        return value.attr
    return None


def _enum_member_truthiness_findings() -> list:
    """Every ``<variable>.Success``-style access in the package.

    Enum classes are CamelCase (``RedfishApiRespond``, ``TaskStatus``), so a
    member fetched from a capitalized name is a legitimate class reference;
    fetched from a lowercase name it is a respond value being misused.
    """
    findings = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            if node.attr not in RESPOND_MEMBERS:
                continue
            owner = _accessed_on(node)
            if owner is None or owner[:1].isupper():
                continue
            rel = path.relative_to(PACKAGE_ROOT.parent)
            findings.append(f"{rel}:{node.lineno} {owner}.{node.attr}")
    return findings


def _parent_map(tree):
    """Map each AST child to its direct parent."""
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _enclosing_function(node, parents):
    """Return the function containing node, if any."""
    cursor = node
    while cursor is not None:
        if isinstance(cursor, ast.FunctionDef | ast.AsyncFunctionDef):
            return cursor
        cursor = parents.get(cursor)
    return None


def _function_arg_names(function):
    """Return all positional and keyword-only argument names for a function."""
    if function is None:
        return set()
    return {
        arg.arg
        for arg in function.args.args + function.args.kwonlyargs
    }


def _unguarded_direct_mutation_findings() -> list[str]:
    """Direct write helpers in command modules without an explicit guard arg."""
    findings = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        if path.name == "redfish_manager_base.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = _parent_map(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in MUTATING_HELPERS:
                continue
            function = _enclosing_function(node, parents)
            if _function_arg_names(function) & MUTATION_GUARD_ARGS:
                continue
            rel = path.relative_to(PACKAGE_ROOT.parent)
            name = function.name if function is not None else "<module>"
            findings.append(f"{rel}:{node.lineno} {name} {node.func.attr}")
    return findings


def _escaping_finally_statements(stmts, in_loop=False):
    """Yield return/break/continue statements that would escape a finally.

    Walks the statement list of a ``finally`` body. Nested function and
    class definitions open a new scope, so nothing inside them escapes;
    ``break``/``continue`` inside a loop that starts within the ``finally``
    bind to that loop and are equally harmless.

    :param stmts: statement list to scan (a ``finalbody`` or nested block).
    :param in_loop: True once inside a loop that started within the finally.
    """
    for node in stmts:
        if isinstance(node, ast.Return):
            yield node
        elif isinstance(node, ast.Break | ast.Continue):
            if not in_loop:
                yield node
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        elif isinstance(node, ast.For | ast.AsyncFor | ast.While):
            yield from _escaping_finally_statements(node.body, in_loop=True)
            yield from _escaping_finally_statements(node.orelse, in_loop=in_loop)
        elif isinstance(node, ast.Match):
            for case in node.cases:
                yield from _escaping_finally_statements(case.body, in_loop=in_loop)
        else:
            blocks = [
                getattr(node, field, [])
                for field in ("body", "orelse", "finalbody", "handlers")
            ]
            for block in blocks:
                if block and isinstance(block[0], ast.ExceptHandler):
                    for handler in block:
                        yield from _escaping_finally_statements(
                            handler.body, in_loop=in_loop)
                else:
                    yield from _escaping_finally_statements(block, in_loop=in_loop)


def _return_in_finally_findings() -> list[str]:
    """Every control-flow statement escaping a ``finally`` in the package."""
    findings = set()
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(PACKAGE_ROOT.parent)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            for stmt in _escaping_finally_statements(node.finalbody):
                kind = type(stmt).__name__.lower()
                findings.add(f"{rel}:{stmt.lineno} {kind} in finally")
    return sorted(findings)


def test_no_enum_member_truthiness_on_respond_values():
    """No module may test an API respond value via member attribute access.

    A finding here means code like ``if api_resp.Success:`` — always truthy,
    treating failures as success. Compare with ``==`` or ``in`` against
    ``RedfishApiRespond`` members instead.
    """
    assert _enum_member_truthiness_findings() == []


def test_the_guard_actually_detects_the_bad_shape():
    """The checker must recognize the exact pattern that shipped as a bug."""
    bad = ast.parse("result = 1 if api_resp.Success or api_resp.Ok else 0")
    hits = [
        node for node in ast.walk(bad)
        if isinstance(node, ast.Attribute) and node.attr in RESPOND_MEMBERS
    ]
    assert len(hits) == 2


def test_new_direct_mutation_calls_are_guarded_or_baselined():
    """New direct write helpers must expose a dry-run/confirm style guard.

    The legacy baseline keeps this from rewriting older commands as part of an
    unrelated feature, while still making new unguarded direct writes fail CI.
    """
    assert set(_unguarded_direct_mutation_findings()) == LEGACY_UNGUARDED_MUTATION_CALLS


def test_mutation_guard_detector_recognizes_unguarded_write():
    """The mutation helper checker detects a direct unguarded PATCH call."""
    tree = ast.parse("""
def execute(self):
    return self.base_patch("/redfish/v1/Systems/1", payload={})
""")
    parents = _parent_map(tree)
    call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call))
    function = _enclosing_function(call, parents)

    assert function.name == "execute"
    assert not (_function_arg_names(function) & MUTATION_GUARD_ARGS)


def test_no_return_break_continue_in_finally():
    """No module may end a ``finally`` block with escaping control flow.

    A finding here means a ``return``/``break``/``continue`` whose target is
    outside the ``finally`` — it swallows in-flight exceptions and raises
    ``SyntaxWarning`` on Python 3.14 (PEP 765). Move the statement after the
    ``try`` statement instead.
    """
    assert _return_in_finally_findings() == []


def _findings_for_snippet(snippet: str) -> list[str]:
    """Run the finally-escape detector over one parsed snippet."""
    tree = ast.parse(snippet)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            found.extend(_escaping_finally_statements(node.finalbody))
    return [type(stmt).__name__ for stmt in found]


def test_finally_detector_recognizes_the_shipped_shape():
    """The detector flags the exact ``finally: return`` shape that shipped."""
    assert _findings_for_snippet("""
def parse(resp):
    out = 1
    try:
        out = resp.json()
    except ValueError:
        pass
    finally:
        return out
""") == ["Return"]


def test_finally_detector_flags_loop_escapes_and_nested_blocks():
    """``break`` escaping via an ``if`` inside ``finally`` is still flagged."""
    assert _findings_for_snippet("""
def drain(items):
    for item in items:
        try:
            item.close()
        finally:
            if item.bad:
                break
""") == ["Break"]


def test_finally_detector_flags_continue_escape():
    """``continue`` targeting a loop outside the ``finally`` is flagged."""
    assert _findings_for_snippet("""
def retry(items):
    for item in items:
        try:
            item.send()
        finally:
            continue
""") == ["Continue"]


def test_finally_detector_flags_return_in_nested_try_finally():
    """A ``return`` in an inner ``finally`` escapes both enclosing trys.

    The helper visits every ``Try`` node independently, so the one statement
    is reported once for the outer ``finally`` and once for the inner; the
    package sweep dedupes by file and line.
    """
    assert _findings_for_snippet("""
def close(conn):
    try:
        conn.flush()
    finally:
        try:
            conn.close()
        finally:
            return None
""") == ["Return", "Return"]


def test_finally_detector_ignores_bound_statements():
    """Statements bound inside the finally itself are not findings.

    Covers a ``return`` after (not inside) the ``finally``, a ``return`` in
    a nested ``def``, and a ``break`` targeting a loop opened inside the
    ``finally`` body.
    """
    assert _findings_for_snippet("""
def parse(resp):
    out = 1
    try:
        out = resp.json()
    finally:
        def flush():
            return None
        for chunk in out:
            if not chunk:
                break
    return out
""") == []
