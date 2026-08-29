"""Gate: environment is read in ONE loader, nowhere else.

Application code must receive canonical config from redfish_ctl/config.py, not
re-derive it from the environment at each call site. This gate is a PLAIN ban
with no baseline: a raw env read - ``os.getenv(...)``, ``os.environ[...]``/
``.get``/``.setdefault``, or the ``env_first(...)`` primitive - is forbidden in
any git-tracked ``redfish_ctl/**.py`` other than the loader itself.

    python3 tools/config_loader_gate.py

Stronger than name-scanning (repo.no-ghost-env): that checks env-var *names*;
this forces *centralization* so there is exactly one place env is read. There is
no grandfathering - every scattered read must migrate into the loader and expose
a config accessor. Exits 0 when clean, 1 listing every offender.

Author Mus spyroot@gmail.com
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

# The loader - the only module allowed to read the environment.
_LOADER = "redfish_ctl/config.py"
# A raw env read. env_first is matched as a CALL (``env_first(``) so the
# re-export import line in redfish_shared.py is not flagged.
_READ = re.compile(r"os\.getenv\(|os\.environ\b|\benv_first\(")


def _violations() -> list[str]:
    """Return ``path:line`` for every raw env read outside the loader.

    :return: sorted ``"path:line"`` strings, one per offending source line.
    """
    out: list[str] = []
    files = subprocess.check_output(
        ["git", "ls-files", "redfish_ctl/*.py", "redfish_ctl/**/*.py"]).decode().split()
    for f in files:
        if f == _LOADER:
            continue
        for i, line in enumerate(pathlib.Path(f).read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if _READ.search(code):
                out.append(f"{f}:{i}")
    return sorted(out)


def main() -> int:
    """Report every out-of-loader env read.

    :return: 0 when no offender is found, 1 when at least one exists.
    """
    viol = _violations()
    for v in viol:
        print(f"config-loader: {v} - env read outside the loader; move it into "
              f"{_LOADER} and expose a config accessor")
    if viol:
        print(f"config-loader: {len(viol)} offending env read(s) outside {_LOADER}")
        return 1
    print(f"config-loader: clean (env read only in {_LOADER})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
