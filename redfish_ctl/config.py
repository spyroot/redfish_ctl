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
import re
import warnings
from typing import Optional

# A name whose value must never appear in an error message.
_SECRET_HINT = re.compile(r"PASSWORD|TOKEN|SECRET|KEY|CREDENTIAL", re.IGNORECASE)


class ConfigurationConflict(RuntimeError):
    """Two names for one setting are set to different values.

    Raised by :func:`env_first` when a canonical name and its deprecated alias
    (or any two names for the same setting) hold different values, so no silent
    override can pick a winner. See the registry specs/config/environment.yaml.
    """


def _redacted(name: str, value: str) -> str:
    """Render ``name=value`` for an error, hiding secret values.

    :param name: the environment variable name.
    :param value: its value.
    :return: ``"NAME=value"``, or ``"NAME=<redacted>"`` for a secret-looking name.
    """
    return f"{name}=<redacted>" if _SECRET_HINT.search(name) else f"{name}={value}"


def env_first(*names: str, default: Optional[str] = None) -> Optional[str]:
    """Resolve one setting from its names, canonical first, conflict-aware.

    Legacy resolution, defined once (see specs/config/environment.yaml). Pass the
    canonical ``REDFISH_*`` name first, then any deprecated ``IDRAC_*`` alias:

    * only the canonical set -> its value;
    * only a legacy alias set -> its value, with a ``DeprecationWarning``;
    * both set to the same value -> the canonical value (legacy ignored);
    * both set to *different* values -> :class:`ConfigurationConflict` (no silent
      override); an empty value counts as set, so it too is a real value.

    :param names: variable names for one setting, canonical first.
    :param default: value returned when none of ``names`` is set.
    :return: the resolved value, or ``default`` when none is set.
    :raises ConfigurationConflict: two names hold different values.
    """
    present = [(n, os.environ[n]) for n in names if n in os.environ]
    if not present:
        return default
    if len({v.strip() for _, v in present}) > 1:
        lines = "\n".join(f"  {_redacted(n, v)}" for n, v in present)
        raise ConfigurationConflict(
            f"Configuration conflict:\n{lines}\n\nUse only {names[0]}.")
    winner, value = present[0]
    if winner != names[0]:
        warnings.warn(
            f"{winner} is a deprecated alias for {names[0]}; set {names[0]} instead",
            DeprecationWarning, stacklevel=2)
    return value
