"""Redfish query-parameter builder.

Builds the standard Redfish query string for a GET request — ``$select``,
``$filter``, ``$expand``, ``$top`` and ``only`` — with validation. These move
work to the server (smaller, faster responses), which matters at fleet scale.

Vendor note: some services (Dell iDRAC) accept only ONE query parameter per URI.
Pass ``one_param_per_uri=True`` (or a vendor capability profile) to enforce that.

Author Mus spyroot@gmail.com
"""
from typing import List, Optional, Union
from urllib.parse import quote

# Characters that are meaningful in Redfish query syntax and must stay literal
# (operators, grouping, quoting); only spaces and other unsafe chars get encoded.
_SAFE = "()'$*,=:+-"


class RedfishQuery:
    """A validated Redfish query parameter set for a single GET.

    Examples
    --------
    >>> RedfishQuery(select=["ProcCStates", "SysMemSize"]).to_query_string()
    '?$select=ProcCStates,SysMemSize'
    >>> RedfishQuery(top=5).to_query_string()
    '?$top=5'
    >>> RedfishQuery(expand=True, expand_levels=2).to_query_string()
    '?$expand=*($levels=2)'
    """

    def __init__(
        self,
        select: Optional[Union[List[str], str]] = None,
        filter: Optional[str] = None,
        expand: Optional[Union[bool, str]] = None,
        expand_levels: int = 1,
        top: Optional[int] = None,
        only: bool = False,
    ):
        """Initialize a Redfish query parameter set.

        :param select: property name(s) for ``$select``; a list or comma string.
        :param filter: expression for ``$filter``.
        :param expand: ``$expand`` mode; True expands all (``*``) or a string
            names the expansion.
        :param expand_levels: depth for ``$expand`` (``$levels``); defaults to 1.
        :param top: page size for ``$top``.
        :param only: when True, add the Redfish ``only`` parameter.
        """
        self.select = select
        self.filter = filter
        self.expand = expand
        self.expand_levels = expand_levels
        self.top = top
        self.only = bool(only)

    def is_empty(self) -> bool:
        """True when no query parameter is set.

        :return: True if no query parameter is active.
        """
        return not self._active_names()

    def _active_names(self) -> List[str]:
        """List the query parameters that are currently set.

        :return: the names (e.g. ``$select``, ``$top``) of the active parameters.
        """
        names = []
        if self.select:
            names.append("$select")
        if self.filter:
            names.append("$filter")
        if self.expand:
            names.append("$expand")
        if self.top is not None:
            names.append("$top")
        if self.only:
            names.append("only")
        return names

    def _validate(self, one_param_per_uri: bool) -> None:
        """Validate the parameter values and cardinality.

        :param one_param_per_uri: when True, allow at most one active parameter.
        :raises ValueError: if ``$top`` or ``$expand`` levels are out of range,
            or more than one parameter is set while ``one_param_per_uri`` is True.
        """
        if self.top is not None and (not isinstance(self.top, int) or self.top < 0):
            raise ValueError("$top must be an integer >= 0")
        if self.expand_levels is not None and (
            not isinstance(self.expand_levels, int) or self.expand_levels < 1
        ):
            raise ValueError("$expand levels must be an integer >= 1")
        active = self._active_names()
        if one_param_per_uri and len(active) > 1:
            raise ValueError(
                "this service supports only one query parameter per URI; "
                f"got {', '.join(active)}"
            )

    def _pairs(self) -> List[str]:
        """Build the encoded ``key=value`` fragments for the active parameters.

        :return: the URL-encoded query fragments in Redfish order.
        """
        pairs: List[str] = []
        if self.select:
            value = self.select if isinstance(self.select, str) else ",".join(self.select)
            pairs.append("$select=" + quote(value, safe=_SAFE))
        if self.filter:
            pairs.append("$filter=" + quote(self.filter, safe=_SAFE))
        if self.expand:
            mode = self.expand if isinstance(self.expand, str) else "*"
            pairs.append(f"$expand={mode}($levels={self.expand_levels})")
        if self.top is not None:
            pairs.append(f"$top={self.top}")
        if self.only:
            pairs.append("only")
        return pairs

    def to_query_string(self, one_param_per_uri: bool = False) -> str:
        """Return the leading ``?...`` query string, or ``""`` when empty.

        :param one_param_per_uri: when True, enforce at most one query parameter.
        :return: the ``?...`` query string, or an empty string when no parameter
            is set.
        :raises ValueError: if validation fails (see :meth:`_validate`).
        """
        self._validate(one_param_per_uri)
        pairs = self._pairs()
        if not pairs:
            return ""
        return "?" + "&".join(pairs)

    def apply(self, url: str, one_param_per_uri: bool = False) -> str:
        """Append the query string to ``url`` (assumes ``url`` has no query yet).

        :param url: base URL with no existing query string.
        :param one_param_per_uri: when True, enforce at most one query parameter.
        :return: ``url`` with the query string appended.
        """
        return url + self.to_query_string(one_param_per_uri)
