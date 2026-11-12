"""Paperless background tasks: uploads and PDF operations run as tasks, and clients follow them.

Task ids are the Celery UUIDs Paperless returns when it accepts work, not database ids.
"""

from datetime import datetime
from typing import Annotated, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

type TaskState = Literal["pending", "started", "success", "failure", "revoked"]

# The states a task never leaves.
FINAL_STATES: Final[frozenset[TaskState]] = frozenset({"success", "failure", "revoked"})

# Output models are tool output schemas: a one-line class docstring each, and field descriptions
# only where the name does not say enough.


class TaskStatus(BaseModel):
    """A Paperless task as it stands."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    task_id: str
    task_type: Annotated[str, Field(description="E.g. consume_file (an upload).")]
    state: Annotated[TaskState, Field(description="pending, started, success, failure or revoked.")]
    message: Annotated[str | None, Field(description="Why the task failed; null otherwise.")]
    document_ids: Annotated[
        tuple[int, ...],
        Field(description="The documents the task created or changed, once known; may be several."),
    ]
    created: datetime
    finished: Annotated[datetime | None, Field(description="Null while the task is running.")]

    @property
    def is_final(self) -> bool:
        """Whether the task has ended, successfully or not."""
        return self.state in FINAL_STATES


type Wait = Literal["not_requested", "finished", "timed_out"]


class TaskReport(TaskStatus):
    """A task, and what waiting for it did."""

    wait: Annotated[
        Wait,
        Field(
            description=(
                "not_requested: wait_seconds was 0, so the task was read once; finished: the task "
                "ended within the wait (see state); timed_out: still running after "
                "waited_seconds, ask again."
            )
        ),
    ]
    waited_seconds: float
