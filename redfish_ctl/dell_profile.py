"""Dell (iDRAC) vendor profile: the chokepoints that are genuinely Dell.

Everything relocated here is Dell-evidenced by the three mechanical signals
(v1.1.0 git era; the ``Dell*``/``Idrac*`` enum naming; the Dell crawl map):
the 201=Created status row, the Dell error envelope, the ``JID_`` job-id
body-scrape, and the OEM job-queue polling semantics. Non-Dell connections
never see these — :class:`~redfish_ctl.vendor_profile.DmtfProfile` and its
siblings carry the DMTF forms only.

Method bodies marked CHIP are relocated verbatim-in-semantics from
``idrac_manager.py``/``redfish_manager.py`` by the implementation pass; each
seam names its exact source anchor. Dell capability is NEVER reduced here
(architecture.yaml ``no_capability_ceiling``): this module is where all of it
lives, unleaked.

Author Mus spyroot@gmail.com
"""
from __future__ import annotations

from .vendor_profile import DmtfProfile, register_profile


class DellProfile(DmtfProfile):
    """Dell chokepoint overrides on the shared DMTF base."""

    vendor = "dell"

    def decode_status(self, status_code: int):
        """Fold an HTTP status into the signal, WITH Dell's 201=Created row.

        CHIP: relocate ``_http_code_mapping`` from ``idrac_manager.py:186-191``
        (the full four-row map including 201 -> RedfishApiRespond.Created; the
        generic enum has no Created — see architecture.yaml state_decode).

        :param status_code: HTTP status from a Dell BMC response.
        :return: the ``RedfishApiRespond`` signal (Created possible here only).
        :raises NotImplementedError: until the implementation pass lands.
        """
        raise NotImplementedError("CHIP: relocate _http_code_mapping "
                                  "(idrac_manager.py:186-191, incl. 201)")

    def error_handler(self, response, expected=None):
        """Decode a response per Dell semantics (iDRAC error envelope).

        CHIP: relocate the Dell ``default_error_handler`` override —
        source: ``idrac_manager.py:555-584`` (parse_error + typed raises with
        the IDRAC.2.x registry envelope).

        :param response: the ``requests.Response`` to decode.
        :param expected: optional expected-status override(s).
        :return: the decoded signal (matching the current chokepoint contract).
        :raises NotImplementedError: until the implementation pass lands.
        """
        raise NotImplementedError("CHIP: relocate Dell default_error_handler "
                                  "(idrac_manager.py:555-584)")

    def parse_task_id(self, response):
        """Extract a job id: Location header first, then the JID_ body-scrape.

        CHIP: relocate the ``JID_`` regex from ``redfish_manager.py:1072``
        (moving it OUT of the neutral layer pays the known_debt row) and
        compose it as: try the DMTF Location form via ``super()``, fall back
        to the Dell body scrape.

        :param response: the response carrying Location header and/or body.
        :return: the job id (``JID_...``) or task id, or None.
        :raises NotImplementedError: until the implementation pass lands.
        """
        raise NotImplementedError("CHIP: relocate JID_ scrape "
                                  "(redfish_manager.py:1072) behind super()")

    def fetch_task(self, manager, task_id: str, **kwargs):
        """Poll a Dell job/task to terminal state (OEM job queue semantics).

        CHIP: relocate the Dell polling body from ``idrac_manager.py:407-553``
        (tqdm progress via DMTF PercentComplete, Retry-After handling, the
        ``_task_state_mapping``/``_job_state_mapping`` dicts from
        ``idrac_manager.py:195-228``). Transport stays on ``manager``.

        :param manager: the connection's manager (transport provider).
        :param task_id: the ``JID_``/task id to poll.
        :return: the terminal task payload (matching the current contract).
        :raises NotImplementedError: until the implementation pass lands.
        """
        raise NotImplementedError("CHIP: relocate Dell fetch_task "
                                  "(idrac_manager.py:407-553 + state maps :195-228)")

    def get_task_state(self, manager, task_id: str, **kwargs):
        """Read a Dell job/task state (IdracTaskState vocabulary).

        CHIP: relocate from ``idrac_manager.py:374`` (the Dell override).

        :param manager: the connection's manager (transport provider).
        :param task_id: the id to read.
        :return: the task state (matching the current contract).
        :raises NotImplementedError: until the implementation pass lands.
        """
        raise NotImplementedError("CHIP: relocate Dell get_task_state "
                                  "(idrac_manager.py:374)")

    def get_job(self, manager, job_id: str, **kwargs):
        """Read one Dell job from the OEM job queue.

        CHIP: relocate from ``idrac_manager.py:337`` (Dell ``/Oem/Dell/Jobs``).

        :param manager: the connection's manager (transport provider).
        :param job_id: the ``JID_`` id to read.
        :return: the job payload (matching the current contract).
        :raises NotImplementedError: until the implementation pass lands.
        """
        raise NotImplementedError("CHIP: relocate Dell get_job "
                                  "(idrac_manager.py:337)")


register_profile("dell", DellProfile)
