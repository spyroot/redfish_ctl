import collections

from .redfish_manager import RedfishManager


class IloManager(RedfishManager):
    """HP iLO manager: interacts with a Redfish endpoint via the REST API.

    The shared connection properties (host, username, password, port, insecure,
    is_http) live in :class:`RedfishManager` and are inherited; iLO-specific
    behavior belongs in method overrides, never a copied constructor.
    """

    _registry = collections.defaultdict(dict)
