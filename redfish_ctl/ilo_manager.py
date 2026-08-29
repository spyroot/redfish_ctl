import collections

from .redfish_manager import CommandResult, RedfishManager


class IloManager(RedfishManager):
    """HP iLO manager: interacts with a Redfish endpoint via the REST API.

    The shared connection properties (host, username, password, port, insecure,
    is_http) live in :class:`RedfishManager` and are inherited; iLO-specific
    behavior belongs in method overrides, never a copied constructor.
    """

    _registry = collections.defaultdict(dict)

    def execute(self, **kwargs) -> CommandResult:
        """Provide the manager-root placeholder required by command dispatch.

        :param kwargs: unused command arguments.
        :return: no direct manager command result; concrete commands override it.
        """
        pass
