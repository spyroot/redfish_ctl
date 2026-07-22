"""Round-trip helper for live mutating tests.

Every live PATCH must prove value restoration: capture the pre-value, apply the
new value, assert the read-back matches, then restore the pre-value and assert
the read-back matches again. Restoration runs in ``finally``, so a failing
assertion still puts the BMC back. The ``repo.live-mutation-roundtrip`` gate
(scripts/gates/repository/live-mutation-roundtrip.sh) rejects any live test
that PATCHes without this helper.

Author Mus spyroot@gmail.com
"""
from __future__ import annotations

from typing import Any


class RoundTripError(AssertionError):
    """Raised when a read-back does not match the value just written.

    Subclasses AssertionError so pytest renders it as a plain test failure
    rather than an error, while the distinct type keeps the failure reason
    (behavioural read-back mismatch) identifiable to the repair-path rule.
    """


def _read_attr(manager, resource: str, attr: str) -> Any:
    """Read one attribute of a Redfish resource.

    :param manager: IDracManager-derived instance issuing the GET.
    :param resource: Redfish resource path, e.g. ``/redfish/v1/Systems/1``.
    :param attr: top-level attribute name in the resource JSON.
    :return: the attribute value, or None when absent.
    """
    result = manager.base_query(resource)
    data = result.data if result is not None else None
    if not isinstance(data, dict):
        raise RoundTripError(f"read of {resource} returned no JSON body")
    return data.get(attr)


def live_roundtrip(manager, resource: str, attr: str, new_value: Any,
                   **patch_kwargs: Any) -> None:
    """Capture -> set -> assert -> restore -> assert for one attribute.

    The restore PATCH runs in ``finally``: an assertion failure after the first
    PATCH still restores the captured value, so a red test cannot leave the BMC
    mutated. The final read-back must equal the captured pre-value, which is
    the property the gate exists to prove.

    Restore is attempted whenever a PATCH has been dispatched, not only when it
    returned cleanly. A networked PATCH can reach the BMC and be applied, then
    raise while the client reads the response (timeout, reset); writing the
    captured value back is idempotent if the write never landed and essential
    if it did, so the safe posture is to always restore once dispatched.

    :param manager: IDracManager-derived instance issuing GET/PATCH.
    :param resource: Redfish resource path holding the attribute.
    :param attr: top-level attribute name to mutate.
    :param new_value: reversible value to write and then roll back.
    :param patch_kwargs: forwarded to both PATCH calls — e.g.
        ``expected_status=200`` for BMCs that answer 200 instead of 204.
    :raises RoundTripError: when any read-back mismatches, or the resource
        yields no JSON body.
    """
    pre_value = _read_attr(manager, resource, attr)
    dispatched = False
    try:
        dispatched = True
        manager.base_patch(resource, payload={attr: new_value}, **patch_kwargs)
        got = _read_attr(manager, resource, attr)
        if got != new_value:
            raise RoundTripError(
                f"{resource}.{attr}: wrote {new_value!r}, read back {got!r}")
    finally:
        if dispatched:
            manager.base_patch(resource, payload={attr: pre_value},
                               **patch_kwargs)
            back = _read_attr(manager, resource, attr)
            if back != pre_value:
                raise RoundTripError(
                    f"{resource}.{attr}: restore wrote {pre_value!r}, "
                    f"read back {back!r} — BMC LEFT MODIFIED")
