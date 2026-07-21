"""Gate: no ghost environment variables — every read is declared, exactly.

Every environment variable the code reads must be listed by its EXACT name in
the registry specs/config/environment.yaml. No prefix allowlist: a new
``OTEL_SOME_NEW_TIMEOUT`` or ``SPLUNK_ANYTHING`` cannot become a permitted ghost
just because its prefix looks standard — it must be a reviewed registry edit.

    python3 tools/no_ghost_env_gate.py

The declared set is the union of every registry section except ``retired``
(using a retired name is caught by repo.no-legacy-resurrection). A read of an
undeclared name fails.

Author Mus spyroot@gmail.com
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

import yaml

# Reads: os.environ["X"] / os.environ.get("X") / os.getenv("X") / env_first("A","B",...)
_READS = [
    re.compile(r'os\.environ\[\s*["\']([A-Z][A-Z0-9_]+)["\']'),
    re.compile(r'os\.environ\.get\(\s*["\']([A-Z][A-Z0-9_]+)["\']'),
    re.compile(r'os\.getenv\(\s*["\']([A-Z][A-Z0-9_]+)["\']'),
]
_ENV_FIRST = re.compile(r'env_first\(\s*((?:["\'][A-Z][A-Z0-9_]+["\']\s*,?\s*)+)')
_NAME = re.compile(r'["\']([A-Z][A-Z0-9_]+)["\']')
_REGISTRY = pathlib.Path(__file__).resolve().parent.parent / "specs" / "config" / "environment.yaml"


def _registry() -> set[str]:
    """Return every exact env-var name declared in the registry.

    The union of all sections except ``retired``: application (canonical,
    legacy_aliases, secrets_and_config, interpreted_external, components),
    external, and system.

    :return: the declared env-var names.
    """
    reg = yaml.safe_load(_REGISTRY.read_text(encoding="utf-8"))
    names: set[str] = set()
    app = reg.get("application", {}) or {}
    for section in ("canonical", "legacy_aliases", "secrets_and_config", "interpreted_external"):
        names |= set((app.get(section) or {}).keys())
    for group in (app.get("components") or {}).values():
        names |= set(group)
    names |= set((reg.get("external") or {}).keys())
    names |= set((reg.get("system") or {}).keys())
    return names


def _read_names() -> set[str]:
    """Return every env-var name the redfish_ctl package reads.

    :return: distinct env-var names referenced in redfish_ctl/**.py.
    """
    files = subprocess.check_output(
        ["git", "ls-files", "redfish_ctl/*.py", "redfish_ctl/**/*.py"]).decode().split()
    names: set[str] = set()
    for f in files:
        raw = pathlib.Path(f).read_text(encoding="utf-8")
        # Strip line comments so a commented-out read is not counted (matches the
        # config-loader gate); line structure is kept for the multi-line env_first.
        t = "\n".join(line.split("#", 1)[0] for line in raw.splitlines())
        for pat in _READS:
            names.update(pat.findall(t))
        for m in _ENV_FIRST.finditer(t):
            names.update(_NAME.findall(m.group(1)))
    return names


def main() -> int:
    """Compare reads against the registry and report undeclared names.

    The registry spans code, scripts, and infra, so a declared name not read in
    redfish_ctl/ is expected, not stale — only the ghost direction is enforced.

    :return: 0 when clean, 1 on a ghost (an undeclared read).
    """
    reg = _registry()
    read = _read_names()
    ghosts = sorted(read - reg)
    for g in ghosts:
        print(f"no-ghost-env: {g} is read but not declared in "
              "specs/config/environment.yaml (add it, exactly, or remove the read)")
    if ghosts:
        print(f"no-ghost-env: {len(ghosts)} ghost(s)")
        return 1
    print("no-ghost-env: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
