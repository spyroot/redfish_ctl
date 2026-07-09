"""Redfish Task state and task status
Based on Redfish spec and correlated with IDRAC API.

Author Mus spyroot@gmail.com
"""

from enum import Enum


class TaskStatus(Enum):
    """https://developer.dell.com/apis/2978/versions/6.xx/openapi.yaml/paths/~1redfish~1v1~1TaskService~1Tasks~1%7BTaskId%7D/get
    """
    Ok = "Ok"
    Warning = "Warning"
    Critical = "Critical"


class TaskState(Enum):
    """https://developer.dell.com/apis/2978/versions/4.xx/docs/101WhatsNew.md
    """
    New = "New"
    Starting = "Starting"
    Running = "Running"
    Suspended = "Suspended"
    Interrupted = "Interrupted"
    Pending = "Pending"
    Stopping = "Stopping"
    Completed = "Completed"
    Killed = "Killed"
    Exception = "Exception"
    Service = "Service"
    Canceling = "Canceling"
    Cancelled = "Cancelled"
    # this not redfish spec.
    # it initials state, and we have no idea about a state.
    Unknown = "Unknown"
