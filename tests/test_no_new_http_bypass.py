"""Repository guard: no NEW direct-HTTP bypass of the traced transport seams.

Release blocker #6 / gate G3 in ``specs/telemetry/gates.md``: every BMC HTTP
call must go through the traced transport primitives so it produces exactly one
CLIENT span. This test freezes the set of package modules allowed to touch
``requests``/``urllib`` directly. A new module making a direct HTTP call fails
here (route it through ``base_query``/``base_post``/``base_patch``/
``base_delete``/``invoke_action`` instead); a listed module that no longer makes
one must be removed from the allowlist so the list never drifts from reality.
"""
from __future__ import annotations

import re
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "redfish_ctl"

# Package-relative modules permitted to touch requests/urllib directly.
ALLOWED_DIRECT_HTTP = {
    # Traced transport seams — where the CLIENT spans are created.
    "redfish_manager.py",
    "redfish_manager_base.py",
    # Telemetry egress: pushes datapoints OUT to SignalFx/OTLP, not a BMC read.
    "telemetry/exporter.py",
    # Firmware image upload (multipart/octet-stream push); traced by the
    # firmware command's own span, kept as an allowed direct-transport site.
    "firmware/cmd_firmware_update.py",
    # Known legacy bypasses tracked for routing through the traced seam
    # (see the telemetry audit); frozen here so no NEW one slips in.
    "cmd_wait.py",
    "discover/cli.py",
    "discovery/net_scan.py",
}

# ``requests.post,`` (callable passed to a helper) and ``requests.get(`` (direct
# call) both count; ``urllib.request.urlopen`` covers the exporter egress form.
_HTTP_CALL = re.compile(
    r"\brequests\.(?:get|post|put|patch|delete|request|Session)\b"
    r"|\burllib\.request\.urlopen\b"
)


def _strip_comment(line: str) -> str:
    """Drop an inline ``#`` comment so mentions in comments do not match.

    :param line: a source line.
    :return: the line with any ``#`` comment removed.
    """
    hash_index = line.find("#")
    return line if hash_index < 0 else line[:hash_index]


def _modules_making_direct_http_calls() -> set[str]:
    """Find package modules that call ``requests``/``urllib`` directly.

    Docstring-only mentions (a bare ``requests.Session`` with no call/reference
    syntax) are excluded by requiring the name to be followed by ``(`` or ``,``.

    :return: package-relative module paths (POSIX style) with a direct call.
    """
    offenders: set[str] = set()
    for path in PACKAGE_ROOT.rglob("*.py"):
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = _strip_comment(raw)
            match = _HTTP_CALL.search(line)
            if not match:
                continue
            tail = line[match.end():].lstrip()
            if tail[:1] in ("(", ","):  # an actual call or callable reference
                offenders.add(path.relative_to(PACKAGE_ROOT).as_posix())
                break
    return offenders


def test_no_new_direct_http_bypass():
    """The set of modules touching HTTP directly equals the frozen allowlist.

    New entries = an untraced BMC-call bypass (route it through the base
    transport primitives). Missing entries = a stale allowlist (remove them).
    """
    found = _modules_making_direct_http_calls()
    new_bypasses = sorted(found - ALLOWED_DIRECT_HTTP)
    stale_allowlist = sorted(ALLOWED_DIRECT_HTTP - found)
    assert not new_bypasses, (
        "New direct-HTTP bypass introduced (gate G3 / release blocker #6): "
        f"{new_bypasses}. Route BMC calls through base_query/base_post/"
        "base_patch/base_delete/invoke_action so each produces one CLIENT span."
    )
    assert not stale_allowlist, (
        "Allowlist is stale — these modules no longer make direct HTTP calls "
        f"and should be removed from ALLOWED_DIRECT_HTTP: {stale_allowlist}."
    )


def test_allowlist_files_exist():
    """Every allowlisted module exists, so the guard cannot silently rot."""
    missing = sorted(m for m in ALLOWED_DIRECT_HTTP if not (PACKAGE_ROOT / m).is_file())
    assert not missing, f"allowlisted modules not found on disk: {missing}"
