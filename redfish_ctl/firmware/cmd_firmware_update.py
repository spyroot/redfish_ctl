"""Flash firmware via Redfish UpdateService update mechanisms (guarded).

    redfish_ctl firmware-update --image_uri http://host/fw.bin              # dry-run
    redfish_ctl firmware-update --image_uri http://host/fw.bin --confirm    # flash
    redfish_ctl firmware-update --image_file fw.bin --confirm               # push upload

Resolves ``#UpdateService.SimpleUpdate`` from the UpdateService's own Actions
block (no hardcoded id) and POSTs {ImageURI, TransferProtocol?} through the
same guarded path. If SimpleUpdate is absent, prefers ``MultipartHttpPushUri``
and then ``HttpPushUri`` for BMCs that accept a local image upload.

DESTRUCTIVE: flashing disrupts/risks the target, so this defaults to a DRY-RUN
(prints the resolved target + payload, POSTs nothing) until ``--confirm``.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from pathlib import Path
from typing import Optional

import requests

from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, RedfishApiRespond, Singleton
from ..redfish_manager import CommandResult
from ..redfish_shared import RedfishApi
from ..telemetry import tracing


class FirmwareUpdate(IDracManager,
                     scm_type=ApiRequestType.FirmwareUpdate,
                     name='firmware-update',
                     metaclass=Singleton):
    """Flash firmware via a discovered UpdateService update endpoint."""

    def __init__(self, *args, **kwargs):
        """Initialize the firmware-update command."""
        super(FirmwareUpdate, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``firmware-update`` subcommand and its safety flags.

        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            '--image_uri', required=False, dest='image_uri', type=str, default=None,
            help="firmware image URI to flash (ImageURI in the payload)")
        cmd_parser.add_argument(
            '--image_file', required=False, dest='image_file', type=str, default=None,
            help="local firmware image file to upload when UpdateService exposes a push URI")
        cmd_parser.add_argument(
            '--transfer_protocol', required=False, dest='transfer_protocol',
            type=str, default=None, help="optional TransferProtocol (HTTP, HTTPS, ...)")
        cmd_parser.add_argument(
            '--confirm', action='store_true', dest='confirm',
            help="actually flash (without it this is a dry-run)")
        cmd_parser.add_argument(
            '--dry_run', action='store_true', dest='dry_run',
            help="force a dry-run preview even if --confirm is given")
        return cmd_parser, "firmware-update", "command flash firmware via UpdateService (guarded)"

    def _update_service_uri(self, do_async):
        """Resolve the UpdateService URI from the service root, with a fallback.

        :param do_async: query the service root asynchronously.
        :return: the UpdateService ``@odata.id``, or the default
            ``{Version}/UpdateService`` when the root exposes no link.
        """
        try:
            root = self.base_query(RedfishApi.Version, do_async=do_async).data or {}
        except Exception:
            root = {}
        link = root.get("UpdateService")
        if isinstance(link, dict) and link.get("@odata.id"):
            return link["@odata.id"]
        return f"{RedfishApi.Version}/UpdateService"

    def _read_update_service(self, do_async):
        """Return ``(uri, resource, error)`` for UpdateService discovery.

        :param do_async: read the UpdateService asynchronously.
        :return: tuple of (UpdateService URI, resource dict, error string or None).
        """
        uri = self._update_service_uri(do_async)
        try:
            resource = self.base_query(uri, do_async=do_async).data or {}
        except Exception as exc:
            return uri, {}, f"failed to read {uri}: {exc}"
        return uri, resource, None

    def _action_result(self, target, payload, actions, do_async, dry_run, confirm):
        """Post the SimpleUpdate action with the same fail-safe response shape.

        :param target: the SimpleUpdate action target URI to POST to.
        :param payload: the request body (ImageURI and optional TransferProtocol).
        :param actions: discovered action metadata carried on the result.
        :param do_async: POST the action asynchronously.
        :param dry_run: when True, preview only and POST nothing.
        :param confirm: required to run a destructive action; when False the
            action is forced to a dry-run preview.
        :return: CommandResult with a dry-run preview or the POST outcome.
        """
        from ..actions.action_policy import Destructiveness, classify

        full = "#UpdateService.SimpleUpdate"
        level = classify(full)
        blocked_reason = None
        effective_dry = bool(dry_run)
        if level == Destructiveness.DESTRUCTIVE and not confirm:
            effective_dry = True
            blocked_reason = "destructive action requires --confirm"

        if effective_dry:
            return CommandResult({
                "dry_run": True,
                "action": full,
                "target": target,
                "payload": payload,
                "level": level.value,
                "blocked": blocked_reason,
            }, actions, None, None)

        result, _ = self.base_post(target, payload=payload, do_async=do_async,
                                   expected_status=202)
        data = result.data if isinstance(result.data, dict) else {"result": result.data}
        data.setdefault("executed", True)
        data.setdefault("action", full)
        data.setdefault("target", target)
        data.setdefault("level", level.value)
        return CommandResult(data, actions, None, result.error)

    @staticmethod
    def _push_target(update_service):
        """Prefer MultipartHttpPushUri, then HttpPushUri, when present.

        :param update_service: the UpdateService resource dict to inspect.
        :return: tuple of (push method name, push URI), or (None, None) when
            neither push URI is present.
        """
        for method in ("MultipartHttpPushUri", "HttpPushUri"):
            target = (update_service or {}).get(method)
            if isinstance(target, str) and target:
                return method, target
        return None, None

    def _post_image_file(self, target, method, image_path):
        """Upload a local image file to the discovered push URI.

        :param target: the push URI path to upload to.
        :param method: the push method (``MultipartHttpPushUri`` or
            ``HttpPushUri``) selecting the upload encoding.
        :param image_path: ``Path`` to the local firmware image to upload.
        :return: the ``requests.Response`` from the upload POST.
        """
        headers = {}
        auth = None
        if self.x_auth is not None:
            headers["X-Auth-Token"] = self.x_auth
        else:
            auth = (self._username, self._password)

        url = f"{self._default_method}{self.redfish_ip}{target}"
        span_attributes = {
            "redfish.action.name": "FirmwareUpload",
            "redfish.action.type": method,
            "redfish.action.target": target,
            "redfish.action.level": "destructive",
        }
        with image_path.open("rb") as image:
            if method == "MultipartHttpPushUri":
                files = {
                    "UpdateFile": (
                        image_path.name,
                        image,
                        "application/octet-stream",
                    )
                }
                return tracing.traced_request(
                    url,
                    "POST",
                    lambda: requests.post(
                        url,
                        files=files,
                        verify=self._is_verify_cert,
                        headers=headers,
                        auth=auth,
                    ),
                    attributes=span_attributes,
                )
            headers["Content-Type"] = "application/octet-stream"
            return tracing.traced_request(
                url,
                "POST",
                lambda: requests.post(
                    url,
                    data=image,
                    verify=self._is_verify_cert,
                    headers=headers,
                    auth=auth,
                ),
                attributes=span_attributes,
            )

    def _push_result(self, target, method, image_file, dry_run, confirm):
        """Preview or execute a push-URI firmware upload.

        :param target: the push URI path to upload to.
        :param method: the push method name being used.
        :param image_file: path to the local firmware image file.
        :param dry_run: when True, preview only and upload nothing.
        :param confirm: when False, force a dry-run preview.
        :return: CommandResult with a dry-run preview, the upload outcome, or an
            error when the file is missing or the upload fails.
        """
        data = {
            "method": method,
            "target": target,
            "image_file": image_file,
            "level": "destructive",
        }
        effective_dry = bool(dry_run) or not confirm
        if effective_dry:
            data.update({
                "dry_run": True,
                "blocked": None if confirm else "destructive update requires --confirm",
            })
            return CommandResult(data, None, None, None)

        image_path = Path(image_file).expanduser()
        if not image_path.is_file():
            return CommandResult(
                data,
                None,
                None,
                f"image_file not found: {image_file}",
            )

        try:
            response = self._post_image_file(target, method, image_path)
            api_resp = self.read_api_respond(response, expected=202)
        except Exception as exc:
            return CommandResult(data, None, None, exc)

        if api_resp == RedfishApiRespond.AcceptedTaskGenerated:
            data["task_id"] = self.job_id_from_header(response)
        else:
            data.update(self.api_success_msg(api_resp))
        data["executed"] = True
        return CommandResult(data, None, None, None)

    def execute(self,
                image_uri: Optional[str] = None,
                image_file: Optional[str] = None,
                transfer_protocol: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Resolve UpdateService and POST the image payload (guarded).

        Returns a dry-run preview unless ``--confirm``; the destructiveness guard
        keeps firmware writes as previews by default.

        :param image_uri: firmware image URI placed in the ImageURI payload for
            SimpleUpdate.
        :param image_file: local firmware image uploaded when the service exposes
            a push URI instead of SimpleUpdate.
        :param transfer_protocol: optional TransferProtocol added to the payload.
        :param confirm: actually flash; without it the command stays a dry-run.
        :param dry_run: force a dry-run preview even when confirm is set.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: run the UpdateService discovery and update request
            asynchronously (subscribes to an event loop).
        :return: CommandResult with a dry-run preview or the update outcome, or an
            error when UpdateService is unreadable or exposes no update mechanism.
        """
        payload = {}
        if image_uri:
            payload["ImageURI"] = image_uri
        if transfer_protocol:
            payload["TransferProtocol"] = transfer_protocol

        uri, update_service, error = self._read_update_service(do_async)
        if error:
            return CommandResult(None, None, None, error)

        actions = self.discover_redfish_actions(self, update_service)
        simple_target = self._flatten_action_targets(update_service).get(
            "#UpdateService.SimpleUpdate"
        )
        if simple_target:
            return self._action_result(
                simple_target,
                payload,
                actions,
                do_async,
                bool(dry_run),
                bool(confirm),
            )

        method, target = self._push_target(update_service)
        available = sorted(set(list(actions.keys()) + list(
            self._flatten_action_targets(update_service).keys()
        )))
        if not target:
            return CommandResult(
                {
                    "action": "firmware-update",
                    "available": available,
                    "update_service": uri,
                },
                actions,
                None,
                "UpdateService exposes no SimpleUpdate, MultipartHttpPushUri, or HttpPushUri",
            )
        if not image_file:
            return CommandResult(
                {
                    "action": "firmware-update",
                    "method": method,
                    "target": target,
                },
                actions,
                None,
                f"{method} requires --image_file",
            )
        return self._push_result(target, method, image_file, bool(dry_run), bool(confirm))
