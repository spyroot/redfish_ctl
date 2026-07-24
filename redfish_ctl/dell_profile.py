"""Dell (iDRAC) vendor profile: the chokepoints that are genuinely Dell.

Everything relocated here is Dell-evidenced by the three mechanical signals
(v1.1.0 git era; the ``Dell*``/``Idrac*`` enum naming; the Dell crawl map):
the 201=Created status row, the Dell error envelope, the ``JID_`` job-id
body-scrape, and the OEM job-queue polling semantics. Non-Dell connections
never see these — :class:`~redfish_ctl.vendor_profile.DmtfProfile` and its
siblings carry the DMTF forms only.

Method bodies are relocated verbatim-in-semantics from
``idrac_manager.py``/``redfish_manager.py``; each method names its source.
Dell capability is NEVER reduced here (architecture.yaml
``no_capability_ceiling``): this module is where all of it lives, unleaked.

Author Mus spyroot@gmail.com
"""
from __future__ import annotations

import logging
import re
import time
from typing import Optional, Tuple

import requests
from tqdm import tqdm

from .cmd_exceptions import (
    AuthenticationFailed,
    ResourceNotFound,
    UnexpectedResponse,
)
from .idrac_shared import REDFISH_API, REDFISH_JSON, JobState, RedfishApiRespond
from .idrac_task_state import IdracTaskState, IdracTaskStatus
from .redfish_exceptions import RedfishForbidden
from .vendor_profile import DmtfProfile, register_profile


class DellProfile(DmtfProfile):
    """Dell chokepoint overrides on the shared DMTF base."""

    vendor = "dell"

    # The Dell status rows relocated from ``IDracManager._http_code_mapping``:
    # the full four-row map including 201 -> Created — the row only Dell adds
    # (the neutral RedfishApiRespond enum has no Created member).
    _status_map = {
        200: RedfishApiRespond.Ok,
        201: RedfishApiRespond.Created,
        202: RedfishApiRespond.AcceptedTaskGenerated,
        204: RedfishApiRespond.Success,
    }

    # Wire JobState string -> enum, relocated from ``IDracManager.__init__``
    # (``_job_state_mapping``), so commands check a state without string
    # branching.
    _job_state_mapping = {
        "Scheduled": JobState.Scheduled,
        "Running": JobState.Running,
        "Completed": JobState.Completed,
        "Downloaded": JobState.Downloaded,
        "Downloading": JobState.Downloading,
        "Scheduling": JobState.Scheduling,
        "Waiting": JobState.Waiting,
        "Failed": JobState.Failed,
        "CompletedWithErrors": JobState.CompletedWithErrors,
        "RebootFailed": JobState.RebootFailed,
        "RebootCompleted": JobState.RebootCompleted,
        "RebootPending": JobState.RebootPending,
        "PendingActivation": JobState.PendingActivation,
        "Unknown": JobState.Unknown,
    }

    # Wire TaskState string -> enum, relocated from ``IDracManager.__init__``
    # (``_task_state_mapping``). Note the Dell model spells the transient
    # cancel state ``Canceling`` (one l), unlike DMTF's ``Cancelling``.
    _task_state_mapping = {
        "New": IdracTaskState.New,
        "Running": IdracTaskState.Starting,
        "Starting": IdracTaskState.Starting,
        "Suspended": IdracTaskState.Suspended,
        "Interrupted": IdracTaskState.Interrupted,
        "Pending": IdracTaskState.Pending,
        "Stopping": IdracTaskState.Stopping,
        "Completed": IdracTaskState.Completed,
        "Killed": IdracTaskState.Killed,
        "Exception": IdracTaskState.Exception,
        "Service": IdracTaskState.Service,
        "Canceling": IdracTaskState.Canceling,
        "Cancelled": IdracTaskState.Cancelled,
    }

    # Wire TaskStatus string -> enum, relocated from ``IDracManager.__init__``
    # (``_task_status_mapping``).
    _task_status_mapping = {
        "ok": IdracTaskStatus.Ok,
        "warning": IdracTaskStatus.Warning,
        "critical": IdracTaskStatus.Critical,
    }

    def decode_status(self, status_code: int) -> RedfishApiRespond:
        """Fold an HTTP status into the signal, WITH Dell's 201=Created row.

        Relocated from ``IDracManager._http_code_mapping`` plus the 2xx fold
        of the Dell ``default_error_handler`` (unmapped 2xx -> Success).

        :param status_code: HTTP status from a Dell BMC response.
        :return: the Dell ``RedfishApiRespond`` signal (Created possible here
            only); ``Error`` for any non-2xx (the raising path is
            :meth:`error_handler`).
        """
        if 200 <= status_code < 300:
            return self._status_map.get(status_code, RedfishApiRespond.Success)
        return RedfishApiRespond.Error

    def error_handler(self, response, manager=None, expected=None):
        """Decode a response per Dell semantics (iDRAC error envelope).

        Body relocated from the Dell ``IDracManager.default_error_handler``:
        a 2xx folds via :meth:`decode_status`; every error code is normalized
        through ``RedfishManager.parse_error`` so the raised exception carries
        the parsed :class:`RedfishError` envelope — never a generic string.
        When ``manager`` is passed, the parsed envelope is also recorded as
        ``manager._redfish_error`` (the attribute callers read after a
        non-raising write path), preserving the manager-side contract.

        :param response: the ``requests.Response`` to decode.
        :param manager: the connection's manager; receives ``_redfish_error``.
        :param expected: reserved expected-status override; unused today.
        :return: the folded ``RedfishApiRespond`` for a 2xx status.
        :raises AuthenticationFailed: on HTTP 401, carrying the parsed error.
        :raises RedfishForbidden: on HTTP 403, carrying the parsed error.
        :raises ResourceNotFound: on HTTP 404, carrying the parsed error.
        :raises UnexpectedResponse: on any other error code, carrying the parsed error.
        """
        code = response.status_code
        if 200 <= code < 300:
            return self.decode_status(code)
        from .redfish_manager import RedfishManager
        redfish_error = RedfishManager.parse_error(response)
        if manager is not None:
            manager._redfish_error = redfish_error
        if code == 401:
            raise AuthenticationFailed(redfish_error)
        if code == 403:
            raise RedfishForbidden(redfish_error)
        if code == 404:
            raise ResourceNotFound(redfish_error)
        raise UnexpectedResponse(redfish_error)

    @staticmethod
    def _scrape_job_id(response) -> Optional[str]:
        """Scrape a Dell ``JID_`` job id out of a response object's text.

        The regex body relocated verbatim from the neutral
        ``RedfishManager.job_id_from_respond`` — moving it here is what makes
        the neutral layer Dell-literal-free (pays the known_debt row in
        architecture.yaml state_decode).

        :param response: requests.models.Response (or any object with a dict).
        :return: the matched ``JID_...`` token, ``None`` when the pattern is
            searched but absent, or an empty string when nothing was searchable.
        """
        try:
            if response is not None and hasattr(response, "__dict__"):
                response_dict = str(response.__dict__)
                if response_dict is not None and len(response_dict) > 0:
                    job_id = re.search("JID_.+?,", response_dict)
                    if job_id is not None:
                        job_id = job_id.group(0)
                    return job_id
        except AttributeError as attr_err:
            logging.debug(f"could not read job id from respond object: {attr_err}")

        return ""

    def parse_task_id(self, response) -> Optional[str]:
        """Extract a job id: Location header first, then the JID_ body-scrape.

        The DMTF Location form (via ``super()``) is the primary path — Dell
        normally does set the header; the ``JID_`` scrape is the Dell-only
        fallback for responses that omit it.

        :param response: the response carrying Location header and/or body.
        :return: the job id (``JID_...``) or task id, empty/None when neither
            source names one.
        """
        job_id = super().parse_task_id(response)
        if job_id:
            return job_id
        return self._scrape_job_id(response)

    def get_task_state(
            self, manager, resp: requests.models.Response
    ) -> Tuple[IdracTaskState, IdracTaskStatus]:
        """Parse response and return task state and status, Dell vocabulary.

        Body relocated from ``IDracManager.get_task_state``: if resp has no
        json payload and JSONDecodeError raised, return Unknown state; if the
        TaskStatus or TaskState key is absent from the response,
        UnexpectedResponse is raised.

        :param manager: the connection's manager (logging provider).
        :param resp: a requests.models.Response object.
        :return: redfish_ctl.IdracTaskState and redfish_ctl.IdracTaskStatus
        :raise redfish_ctl.UnexpectedResponse: If the response body does not
            contain a task state.
        """
        try:
            resp_data = resp.json()
        except requests.exceptions.JSONDecodeError as json_err:
            manager.logger.error(
                f"failed parse response to get a task state. {str(json_err)}"
            )
            return IdracTaskState.Unknown, IdracTaskStatus.Warning

        # dodge case
        if REDFISH_JSON.TaskStatus not in resp_data or REDFISH_JSON.TaskState not in resp_data:
            raise UnexpectedResponse(f"IDRAC returned a {resp_data}, neither task state nor status is present..")

        resp_state = resp_data[REDFISH_JSON.TaskState]
        resp_status = resp_data[REDFISH_JSON.TaskStatus]

        # update state and status.
        task_state = self._task_state_mapping[resp_state]
        task_status = self._task_status_mapping[resp_status.lower()]
        return task_state, task_status

    def fetch_task(self, manager, task_id: str, sleep_time: int = 10,
                   wait_for: int = 0,
                   wait_for_state: Optional[IdracTaskState] = None,
                   timeout: Optional[float] = None, **kwargs) -> IdracTaskState:
        """Poll a Dell job/task to terminal state (OEM job queue semantics).

        Body relocated from ``IDracManager.fetch_task``: consult the OEM
        ``/Oem/Dell/Jobs`` job first (a finished job bounces off without
        polling), then poll ``/redfish/v1/TaskService/Tasks/{id}`` with tqdm
        progress via PercentComplete and ``Retry-After`` handling. Transport
        stays on ``manager``.

        :param manager: the connection's manager (transport provider).
        :param task_id: the ``JID_``/task id to poll.
        :param sleep_time: default sleep between polls; a server ``Retry-After``
            takes precedence when larger.
        :param wait_for: wait for a specific status code instead of 200.
        :param wait_for_state: wait for a specific state (defaults to the
            Unknown sentinel, i.e. wait for completion).
        :param timeout: accepted for cross-vendor signature parity; the Dell
            poll is bounded by the job state, not a wall clock, and does not
            consult it (matching the pre-relocation behavior).
        :return: the last observed state (``IdracTaskState`` or ``JobState``
            for an already-finished job), matching the pre-relocation contract.
        :raise AuthenticationFailed: if the task service returns HTTP 401.
        :raise UnexpectedResponse: on an unknown Dell job state string.
        """
        if wait_for_state is None:
            wait_for_state = IdracTaskState.Unknown

        last_update = 0
        percent_done = 0

        # job might be already done.
        jb = self.get_job(manager, task_id)

        # if job scheduler or scheduling it make sense to wait otherwise we
        # return state; we expect a JobState
        if REDFISH_JSON.JobState in jb:

            current_state = jb[REDFISH_JSON.JobState]
            if current_state not in self._job_state_mapping:
                raise UnexpectedResponse(f"IDRAC returned a {current_state} job type that we don't know.")
            _ = self._job_state_mapping[current_state]
            if current_state == JobState.Scheduled.value or current_state == JobState.Scheduling.value \
                    or current_state == JobState.Running.value:
                manager.logger.info(f"Job {task_id} is {current_state}.. waiting for completion.")
            else:
                manager.logger.info(f"Job {task_id} is {current_state}..bouncing off.")
                return self._job_state_mapping[current_state]

        # in case server will ask to wait.
        retry_after = 0
        # initial state we don't know
        task_state = IdracTaskState.Unknown
        with tqdm(total=100) as pbar:
            while True:
                # /redfish/v1/TaskService/Tasks/{TaskId}
                resp = manager.api_get_call(f"{manager._default_method}{manager.idrac_ip}"
                                            f"{REDFISH_API.Tasks}{task_id}", hdr={})

                if 'Retry-After' in resp.headers:
                    retry_after = int(resp.headers["Retry-After"])
                    manager.logger.info(
                        f"Remote server responded "
                        f"with Retry-After {retry_after}"
                    )
                if resp.status_code == 401:
                    manager.logger.error("task service returned 401")
                    raise AuthenticationFailed("Authentication failed.")
                # if server failed, meanwhile HTTP exception propagate
                # up on the stack.
                if resp.status_code > 499:
                    manager.logger.critical(
                        f"task service return http error code "
                        f"{resp.status_code}"
                    )
                    break
                # Cancellation: A subsequent GET request on the task monitor URI
                # returns either the HTTP 410 Gone or 404 Not Found status code.
                elif resp.status_code == 404 or resp.status_code == 410:
                    manager.logger.info(f"task service returned {resp.status_code}")
                    # at the end we check a state and return it might fail, exception etc.
                    break
                # if client expect something else than 200 or something else, we return result.
                elif 0 < wait_for == resp.status_code:
                    task_state, task_status = self.get_task_state(manager, resp)
                    return task_state
                # As long as the operation is in process, the service shall return the HTTP 202 Accepted status code
                # when the client performs a GET request on the task monitor URI.
                elif resp.status_code == 202:
                    manager.logger.info("task service returned 202")
                    # state acquisition and update state
                    resp_data = resp.json()
                    task_state, task_status = self.get_task_state(manager, resp)
                    manager.logger.info(f"Updating state, new state "
                                        f"{task_state.value}, status {task_status.value}")

                    # update description so caller see.
                    pbar.set_description(task_state.value)
                    if (task_status == IdracTaskStatus.Critical
                            or task_status == IdracTaskStatus.Warning):
                        # we bounce, if status not ok
                        break

                    percent_done = manager.update_progress(resp_data, percent_done)
                    if percent_done > last_update:
                        last_update = percent_done
                        inc = percent_done - pbar.n
                        pbar.update(n=inc)

                    # update retry time, we've been asked
                    if retry_after > sleep_time:
                        sleep_time = retry_after
                    time.sleep(sleep_time)

                # The appropriate HTTP status code, such as but not limited to 200 OK
                # for most operations or 201 Created for POST to create a resource.
                # if client passed wait_for for example 204 we need have handle for 200
                elif resp.status_code == 200:
                    task_state, task_status = self.get_task_state(manager, resp)
                    manager.logger.info(
                        f"Server return status code 200, Task state "
                        f"{task_state.value}, {task_status.value}"
                    )
                    return task_state
                # client wait for specific state
                elif task_state == wait_for_state:
                    manager.logger.info(f"caller asked for wait for a state {wait_for_state.value}")
                    task_state, task_status = self.get_task_state(manager, resp)
                    return task_state
                else:
                    # in all other cases update state and go back sleep.
                    task_state, task_status = self.get_task_state(manager, resp)
                    manager.logger.error("unexpected status code", resp.status_code)
                    if retry_after > sleep_time:
                        sleep_time = retry_after
                    time.sleep(sleep_time)

        return task_state

    def get_job(self, manager, job_id: str, data_type: str = "json",
                do_async: bool = False) -> dict:
        """Query information for particular job from dell oem.

        Body relocated from ``IDracManager.get_job``: respond is information
        about a specific configuration Job scheduled by or being executed by a
        Redfish service's Job Service, read from the Dell OEM job queue.

        :param manager: the connection's manager (transport provider).
        :param job_id: iDRAC job_id JID_744718373591
        :param data_type: json or xml
        :param do_async: note async will subscribe to an event loop.
        :return: the job payload dict.
        """
        headers = {}
        if data_type == "json":
            headers.update(manager.json_content_type)

        r = f"{manager.idrac_members}/Oem/Dell/Jobs/{job_id}"
        return manager.base_query(r, do_expanded=True, do_async=do_async).data


register_profile("dell", DellProfile)
