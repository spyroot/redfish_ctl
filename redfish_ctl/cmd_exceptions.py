class InvalidArgument(Exception):
    pass


class UncommittedPendingChanges(Exception):
    """Must raise if state has Uncommitted changes.
    """
    pass


class InvalidJsonSpec(Exception):
    """Must raise in case json spec is invalid.
    """
    pass


class MissingMandatoryArguments(Exception):
    """Must raise in case argument missing.
    """
    pass


class InvalidArgumentFormat(Exception):
    """Must raise in an argument is invalid format.
    """
    pass


class FailedDiscoverAction(Exception):
    """Must raise if requested action failed to discover.
    i.e. old IDRAC version for example.
    """
    pass


class NoPendingValues(Exception):
    """Must raise if failed discover action.
    """
    pass


class UnsupportedAction(Exception):
    pass


class AuthenticationFailed(Exception):
    pass


class ResourceNotFound(Exception):
    pass


class MissingResource(Exception):
    pass


class UnexpectedResponse(Exception):
    """Must raise if received unexpected response,
    example we got a value for a key that not in redfish spec.
    and we have no idea what to do with it."""
    pass


class TaskIdUnavailable(Exception):
    """Must raise if job , task id unavailable response"""
    pass


class JsonHttpError(Exception):
    """ Root exception about json and http errors.
    This mainly to differentiate positive case.
    i.e. 404 is ok in many case and we except that i.e.
    it not an exception meanwhile if we get something dodgy
    we raise.
    """
    def __init__(self, *args, **kwargs):
        super(JsonHttpError, self).__init__(args, kwargs)
        self.json_error = kwargs.get('json_error')


class PatchRequestFailed(JsonHttpError):
    pass


class DeleteRequestFailed(JsonHttpError):
    pass


class PostRequestFailed(JsonHttpError):
    pass
