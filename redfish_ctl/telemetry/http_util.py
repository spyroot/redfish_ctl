"""Shared HTTP-safety utilities for telemetry backends.

These helpers are generic: any component making a token-bearing HTTPS request
should refuse redirects (so a credential header is never replayed to a redirected
host) and validate that its endpoint is HTTPS. Only the SignalFx writer uses them
today, but nothing here is SignalFx-specific, so they live at the telemetry root
and are shared by all of redfish_ctl.

Author Mus spyroot@gmail.com
"""

from __future__ import annotations

import urllib.parse
import urllib.request


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Fail closed instead of forwarding a token-bearing request across a redirect."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Reject an HTTP redirect before urllib can replay the request.

        :param req: original urllib request object.
        :param fp: response file object supplied by urllib.
        :param code: redirect HTTP status code.
        :param msg: redirect HTTP status message.
        :param headers: redirect response headers.
        :param newurl: URL urllib would otherwise follow.
        :return: never returns; raises :class:`ValueError`.
        :raises ValueError: always, so credential headers stay on the original host.
        """
        raise ValueError("request refused redirect")


def open_no_redirect_request(request: urllib.request.Request, timeout: float):
    """Open a request without following redirects.

    A token sent in a custom header (for example ``X-SF-Token``) must not be
    replayed to a redirected host, so token-bearing requests use a
    redirect-disabled opener.

    :param request: prepared urllib request.
    :param timeout: HTTP timeout in seconds.
    :return: urllib response object suitable for use as a context manager.
    :raises ValueError: if the server returns a redirect response.
    """
    opener = urllib.request.build_opener(NoRedirectHandler)
    return opener.open(request, timeout=timeout)


def require_https_url(url: str, label: str) -> urllib.parse.ParseResult:
    """Return a parsed URL when it is HTTPS, else raise a clear error.

    :param url: URL string to validate.
    :param label: human-readable name for error messages.
    :return: parsed URL for a non-empty HTTPS URL with a network location.
    :raises ValueError: when the URL is missing a host or does not use HTTPS.
    """
    parsed = urllib.parse.urlparse(url or "")
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise ValueError(f"{label} must use https; got {url!r}")
    return parsed
