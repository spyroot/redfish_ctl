"""Structural conventions enforced over the package source.

These tests parse every module under ``redfish_ctl/`` into an AST and fail
on code shapes that are known bugs, so the class of mistake cannot be
reintroduced anywhere — including in files that do not exist yet.

Rule 1 — no enum-member truthiness on API respond values.
``base_post``/``base_patch``/``base_delete`` return an ``IdracApiRespond``
enum member. Testing it with attribute access (``api_resp.Success``)
fetches the always-truthy class member instead of comparing, so an Error
response satisfied success branches; three commands escalated a FAILED
write into a commit with reboot before this was fixed. The only valid
member references are through the enum class itself
(``IdracApiRespond.Success``); comparisons must use ``==``/``in``.

Author Mus spyroot@gmail.com
"""
import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "redfish_ctl"

RESPOND_MEMBERS = {"Success", "Ok", "AcceptedTaskGenerated", "Error"}


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

    Enum classes are CamelCase (``IdracApiRespond``, ``TaskStatus``), so a
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


def test_no_enum_member_truthiness_on_respond_values():
    """No module may test an API respond value via member attribute access.

    A finding here means code like ``if api_resp.Success:`` — always truthy,
    treating failures as success. Compare with ``==`` or ``in`` against
    ``IdracApiRespond`` members instead.
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
