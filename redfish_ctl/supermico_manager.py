import collections

from . import CommandResult
from .redfish_manager import RedfishManager


class SupermicroManager(RedfishManager):
    """Supermicro manager: interacts with a Redfish endpoint via the REST API.

    The shared connection properties (host, username, password, port, insecure,
    is_http) live in :class:`RedfishManager` and are inherited; Supermicro-
    specific behavior belongs in method overrides, never a copied constructor.
    The ABC base was removed — with RedfishManager's abstract ``execute`` it made
    the class non-instantiable, breaking the ``--vendor supermicro`` path.
    """

    _registry = collections.defaultdict(dict)

    def execute(self, **kwargs) -> CommandResult:
        pass