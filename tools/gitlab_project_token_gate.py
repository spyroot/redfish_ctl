#!/usr/bin/env python3
"""Validate the GitLab PROJECT access token behind the ``gitlab.project-token.*`` shared gates.

Four independent checks confirm the CI token is least-privilege and usable:
- ``exists``               — the token authenticates (``GET /user`` → 200 with a username).
- ``api-access``           — it carries API scope (``GET /version`` → 200, not 403).
- ``project-bound``        — its identity is the project bot (``bot: true`` and username ``project_<id>_bot…``).
- ``no-cross-project-access`` — it sees ONLY its own project (membership list is just ``GITLAB_PROJECT_ID``).

Config comes from the environment (``GITLAB_URL``, ``GITLAB_PROJECT_TOKEN``, ``GITLAB_PROJECT_ID``); the
token value is never printed. Live checks hit the GitLab REST API over stdlib urllib; unit tests inject a
fake getter, so the offline suite never touches the network.

    python3 tools/gitlab_project_token_gate.py --check api-access
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def _http_get(url: str, token: str, timeout: int = 10):
    """GET a GitLab API URL, returning ``(status_code, parsed_json_or_None)``; never raises on non-2xx.

    :param url: the full API URL to fetch.
    :param token: the GitLab token sent as the ``PRIVATE-TOKEN`` header (never logged).
    :param timeout: socket timeout in seconds.
    :return: a ``(status, body)`` tuple; ``status`` is 0 on a transport error.
    """
    req = urllib.request.Request(url, headers={"PRIVATE-TOKEN": token})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed internal host)
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except Exception:
        return 0, None


def _config() -> dict:
    """Read gate configuration from the environment.

    :return: a dict with ``api`` base URL, ``token``, and ``project_id``.
    """
    url = os.environ.get("GITLAB_URL", "").rstrip("/")
    return {
        "api": f"{url}/api/v4",
        "url": url,
        "token": os.environ.get("GITLAB_PROJECT_TOKEN", ""),
        "project_id": os.environ.get("GITLAB_PROJECT_ID", ""),
    }


def check_exists(cfg: dict, get=_http_get):
    """Token authenticates and resolves to a user.

    :param cfg: gate config from :func:`_config`.
    :param get: HTTP getter (injected in tests).
    :return: ``(ok, detail)``.
    """
    status, body = get(f"{cfg['api']}/user", cfg["token"])
    return (status == 200 and bool((body or {}).get("username"))), f"/user -> {status}"


def check_api_access(cfg: dict, get=_http_get):
    """Token carries API scope (a metadata endpoint returns 200, not 403).

    :param cfg: gate config from :func:`_config`.
    :param get: HTTP getter (injected in tests).
    :return: ``(ok, detail)``.
    """
    status, _ = get(f"{cfg['api']}/version", cfg["token"])
    return (status == 200), f"/version -> {status} (403 = no api scope)"


def check_project_bound(cfg: dict, get=_http_get):
    """Token identity is the project bot bound to ``GITLAB_PROJECT_ID``.

    :param cfg: gate config from :func:`_config`.
    :param get: HTTP getter (injected in tests).
    :return: ``(ok, detail)``.
    """
    status, body = get(f"{cfg['api']}/user", cfg["token"])
    b = body or {}
    ok = (status == 200 and b.get("bot") is True
          and f"project_{cfg['project_id']}_bot" in (b.get("username") or ""))
    return ok, f"user bot={b.get('bot')} name~project_{cfg['project_id']}_bot"


def check_no_cross_project_access(cfg: dict, get=_http_get):
    """Token sees only its own project (least privilege).

    :param cfg: gate config from :func:`_config`.
    :param get: HTTP getter (injected in tests).
    :return: ``(ok, detail)``.
    """
    status, body = get(f"{cfg['api']}/projects?membership=true&per_page=100", cfg["token"])
    if status != 200 or not isinstance(body, list):
        return False, f"/projects -> {status}"
    ids = {str(p.get("id")) for p in body}
    return (ids in ({str(cfg["project_id"])}, set())), \
        f"visible ids={sorted(ids)} expected only {cfg['project_id']}"


CHECKS = {
    "exists": check_exists,
    "api-access": check_api_access,
    "project-bound": check_project_bound,
    "no-cross-project-access": check_no_cross_project_access,
}


def main(argv: list[str] | None = None) -> int:
    """Run one named check and exit non-zero on failure.

    :param argv: optional argument vector (defaults to ``sys.argv``).
    :return: 0 pass, 1 fail, 2 misconfigured.
    """
    ap = argparse.ArgumentParser(description="Validate the GitLab project access token.")
    ap.add_argument("--check", required=True, choices=sorted(CHECKS))
    args = ap.parse_args(argv)

    cfg = _config()
    if not cfg["token"] or not cfg["url"]:
        sys.stderr.write("gitlab.project-token: GITLAB_URL / GITLAB_PROJECT_TOKEN not set\n")
        return 2
    ok, detail = CHECKS[args.check](cfg)
    label = f"gitlab.project-token.{args.check}"
    if ok:
        print(f"{label}: OK ({detail})")
        return 0
    sys.stderr.write(f"{label}: FAIL ({detail})\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
