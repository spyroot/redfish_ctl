"""iDRAC (Dell) task-state and task-status enumerations.

These are Dell's task model: the values are correlated with the iDRAC API
(see the developer.dell.com references on each class) and consumed by the
``IDracManager`` (``idrac_manager.py``) job/task mappings, which carry Dell
quirks such as mapping the ``Running`` string to ``Starting``. They are named
``Idrac*`` on purpose, so the generic ``TaskState`` / ``redfish_task_state.py``
names stay reserved for the DMTF-generic Task Manager that is not yet built.

Author Mus spyroot@gmail.com
"""

from enum import Enum


class IdracTaskStatus(Enum):
    """https://developer.dell.com/apis/2978/versions/6.xx/openapi.yaml/paths/~1redfish~1v1~1TaskService~1Tasks~1%7BTaskId%7D/get
    """
    Ok = "Ok"
    Warning = "Warning"
    Critical = "Critical"


class IdracTaskState(Enum):
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
