"""Process-wide Kubernetes client shared by the redfish_ctl controllers.

kopf dispatches synchronous resource handlers on a ``ThreadPoolExecutor``, so
at fleet scale many handler threads run concurrently. The previous controllers
each called ``config.load_incluster_config()`` / ``config.load_kube_config()``
and built a fresh ``client.CoreV1Api()`` on *every* handler invocation. Both the
config loaders mutate the kubernetes client's process-global default
``Configuration`` singleton, so concurrent handler threads raced on that global
and rebuilt a client (and its connection pool) per poll.

This module loads the kube config exactly once, behind a lock, and hands every
caller the same thread-safe ``CoreV1Api`` (its underlying ``urllib3`` pool is
safe to share across threads). Both the endpoint and node-profile controllers
run in one kopf process and import this module, so they share a single client.

The ``kubernetes`` package is imported lazily inside the seams below: the
offline test suite does not install it, and importing at module load would make
the controllers unimportable in tests. The seams (`_load_kube_config`,
`_build_core_v1_api`) are also the injection points the concurrency tests
monkeypatch to prove the config is loaded once under load without a real cluster.

Author Mus <spyroot@gmail.com>
"""

from __future__ import annotations

import threading
from typing import Any

_LOCK = threading.Lock()
_CORE_V1_API: Any | None = None


def _load_kube_config() -> None:  # pragma: no cover - needs a real cluster/kubeconfig.
    """Load in-cluster config, falling back to a local kubeconfig."""
    from kubernetes import config

    try:
        config.load_incluster_config()
    except Exception:
        config.load_kube_config()


def _build_core_v1_api() -> Any:  # pragma: no cover - needs the kubernetes package.
    """Return a fresh CoreV1Api bound to the loaded configuration."""
    from kubernetes import client

    return client.CoreV1Api()


def get_core_v1_api() -> Any:
    """Return the one process-wide CoreV1Api, loading kube config once.

    Uses double-checked locking so the common path (client already built) never
    takes the lock, while the first concurrent burst of handler threads loads the
    config and builds the client exactly once. Raises ``ImportError`` (or the
    underlying config error) if kubernetes is unavailable; callers that must
    degrade offline catch that and fall back.
    """
    global _CORE_V1_API
    api = _CORE_V1_API
    if api is not None:
        return api
    with _LOCK:
        if _CORE_V1_API is None:
            _load_kube_config()
            _CORE_V1_API = _build_core_v1_api()
        return _CORE_V1_API


def reset_client_cache() -> None:
    """Drop the cached client so the next call rebuilds it.

    Used by tests to isolate the singleton, and available as a hook to force a
    reconnect (e.g. after an in-cluster token rotation).
    """
    global _CORE_V1_API
    with _LOCK:
        _CORE_V1_API = None
