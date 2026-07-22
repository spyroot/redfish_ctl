"""Generic DMTF Redfish task-state and task-status enumerations.

These mirror the DMTF Redfish ``Task`` schema (``#Task.v1_x.Task``) exactly, as
served on ``/redfish/v1/TaskService/Tasks``. They are the generic counterpart to
the Dell task model in ``idrac_task_state.py`` (``IdracTaskState`` /
``IdracTaskStatus``): the Dell model adds a non-spec ``Unknown`` and uses the
misspelled ``Canceling`` (one 'l'), whereas the specification defines
``Cancelling`` (two 'l's) and no ``Unknown``. The DMTF-generic ``RedfishManager``
uses these; a vendor manager that diverges (Dell ``IDracManager``) keeps its own
``Idrac*`` enums and its ``/Oem/Dell/Jobs`` ``JobState``.

Reference: https://www.dmtf.org/standards/REDFISH (Task.v1 schema).

Author Mus spyroot@gmail.com
"""

from enum import Enum


class TaskStatus(Enum):
    """DMTF task health, per the Redfish ``Health`` enumeration.

    The wire value on a ``#Task`` resource's ``TaskStatus`` property; the Dell
    model spells the healthy value ``Ok`` while the specification uses ``OK``.
    """
    OK = "OK"
    Warning = "Warning"
    Critical = "Critical"


class TaskState(Enum):
    """DMTF task state, per the Redfish ``Task.TaskState`` enumeration.

    The full specification set served on ``/redfish/v1/TaskService/Tasks``. Note
    the spec spelling ``Cancelling`` (two 'l's); the Dell model misspells it
    ``Canceling``. There is deliberately no ``Unknown`` member — an unobserved or
    indeterminate state is represented as ``None`` by callers.
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
    Cancelling = "Cancelling"
    Cancelled = "Cancelled"


# Terminal states per the Redfish Task schema: once a task reaches one of these,
# no further state transition occurs, so a blocking poll stops here.
TERMINAL_TASK_STATES = frozenset(
    {
        TaskState.Completed,
        TaskState.Killed,
        TaskState.Cancelled,
        TaskState.Exception,
    }
)
