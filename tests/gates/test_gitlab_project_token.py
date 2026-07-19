"""Cover the GitLab project-token gate checks offline (injected getter — no network).

Each check is exercised on both a well-scoped token (passes) and a mis-scoped one (fails), matching the
four gitlab.project-token.* gates.
"""
from tools import gitlab_project_token_gate as g

CFG = {"api": "https://gl/api/v4", "url": "https://gl", "token": "x", "project_id": "5"}


def _fake(routes):
    """Build a getter that maps a URL substring -> (status, body).

    :param routes: list of ``(substring, status, body)`` rules, first match wins.
    :return: a getter with the ``(url, token, timeout=...)`` signature.
    """
    def get(url, token, timeout=10):
        for frag, status, body in routes:
            if frag in url:
                return status, body
        return 404, None
    return get


def test_exists_pass_and_fail():
    """`exists` passes on an authenticated user and fails on 401."""
    ok, _ = g.check_exists(CFG, _fake([("/user", 200, {"username": "project_5_bot_ab"})]))
    assert ok
    bad, _ = g.check_exists(CFG, _fake([("/user", 401, None)]))
    assert not bad


def test_api_access_distinguishes_403():
    """`api-access` passes on 200 and fails on the 403 no-scope response."""
    assert g.check_api_access(CFG, _fake([("/version", 200, {"version": "1"})]))[0]
    assert not g.check_api_access(CFG, _fake([("/version", 403, None)]))[0]


def test_project_bound_requires_bot_identity():
    """`project-bound` needs bot=true and a project_<id>_bot username."""
    ok, _ = g.check_project_bound(CFG, _fake([("/user", 200, {"bot": True, "username": "project_5_bot_z"})]))
    assert ok
    # a human/user token (not a project bot) fails
    bad, _ = g.check_project_bound(CFG, _fake([("/user", 200, {"bot": False, "username": "alice"})]))
    assert not bad


def test_empty_membership_list_is_not_least_privilege():
    """An empty membership list fails instead of reading as maximal least privilege.

    A token that cannot see even its own project has not proven anything. The gate previously accepted
    the empty set, so a scope change that hides memberships — exactly the case worth catching — was
    indistinguishable from a perfectly-scoped token.
    """
    ok, detail = g.check_no_cross_project_access(CFG, _fake([("/projects", 200, [])]))
    assert not ok
    assert "inconclusive" in detail


def test_membership_pagination_is_followed_to_exhaustion():
    """A cross-project membership on a later page is still detected.

    The gate previously fetched one page of 100; a token with more than 100 memberships could hide the
    leaking project past the page boundary and pass.
    """
    page1 = [{"id": 5}] * 100
    calls = {"n": 0}

    def get(url, token, timeout=10):
        """Return a full first page, then a short second page carrying a foreign project."""
        calls["n"] += 1
        return (200, page1) if "page=1" in url else (200, [{"id": 9}])

    ok, detail = g.check_no_cross_project_access(CFG, get)
    assert calls["n"] >= 2, "pagination stopped at the first page"
    assert not ok
    assert "9" in detail


def test_project_bound_rejects_a_lookalike_username():
    """The bot username is matched as an anchored prefix, not a substring.

    An account merely containing the expected name (``svc_project_5_bot``) is a different identity and
    must not satisfy a least-privilege gate.
    """
    ok, _ = g.check_project_bound(
        CFG, _fake([("/user", 200, {"bot": True, "username": "svc_project_5_bot"})])
    )
    assert not ok


def test_no_cross_project_access_least_privilege():
    """`no-cross-project-access` passes when only the own project is visible, fails otherwise."""
    own = g.check_no_cross_project_access(CFG, _fake([("/projects", 200, [{"id": 5}])]))
    assert own[0]
    leak = g.check_no_cross_project_access(CFG, _fake([("/projects", 200, [{"id": 5}, {"id": 9}])]))
    assert not leak[0]


def test_main_reports_misconfig_without_env(monkeypatch):
    """With no GITLAB_URL/token in the env, the CLI exits 2 (misconfigured), not a crash."""
    monkeypatch.delenv("GITLAB_URL", raising=False)
    monkeypatch.delenv("GITLAB_PROJECT_TOKEN", raising=False)
    assert g.main(["--check", "api-access"]) == 2
