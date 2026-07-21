"""Single configuration loader - the ONLY module that reads the environment.

Application code must receive canonical configuration values from here; it must
never call ``os.getenv``, index ``os.environ``, or call :func:`env_first`
directly. Centralizing env access in one loader means one place defines every
setting, its canonical ``REDFISH_*`` name, its deprecated ``IDRAC_*`` alias, and
its default - instead of the value being re-derived at each call site.

The canonical setting model is specs/config/settings.yaml. :func:`env_first` is
the raw primitive the loader is built on; typed accessors
(``config.protocol.request_timeout`` and friends) are added here as call sites
migrate off direct env reads.

Enforced by tools/config_loader_gate.py (gate ``repo.config-loader``): a raw env
read anywhere outside this module fails the gate.

Author Mus spyroot@gmail.com
"""
from __future__ import annotations

import os
from typing import Optional


def env_first(*names: str, default: Optional[str] = None) -> Optional[str]:
    """Return the value of the first environment variable set among ``names``.

    Lets a setting honor the going-forward ``REDFISH_*`` name first and fall back
    to the legacy ``IDRAC_*`` name during the rename. Pass the ``REDFISH_*`` name
    first.

    :param names: environment variable names to check, in priority order.
    :param default: value returned when none of ``names`` is set.
    :return: the first set variable's value, or ``default`` when none is set.
    """
    for name in names:
        value = os.environ.get(name)
        if value is not None:
            return value
    return default
