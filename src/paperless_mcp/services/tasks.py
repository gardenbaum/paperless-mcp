"""Following a Paperless task: one read, or a bounded wait until it ends.

Uploads and PDF operations run asynchronously; clients follow them with the task id. The wait
polls Paperless on a fixed interval and never longer than ``MAX_WAIT_SECONDS``, so a tool call
stays within what MCP clients tolerate. The clock and the sleeper are injected so the wait is
testable without sleeping.
"""

import asyncio
import time
from typing import TYPE_CHECKING, Final

from paperless_mcp.domain.tasks import TaskReport

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from paperless_mcp.domain.ports import TaskReader
    from paperless_mcp.domain.tasks import TaskStatus, Wait

# The longest a single call waits, and how often the task is read meanwhile.
MAX_WAIT_SECONDS: Final = 30
POLL_SECONDS: Final = 2.0


class TaskService:
    """Reads tasks, optionally waiting for them to end."""

    def __init__(
        self,
        reader: TaskReader,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Read through ``reader``; ``sleep`` and ``clock`` are the seams tests replace."""
        self._reader: TaskReader = reader
        self._sleep: Callable[[float], Awaitable[None]] = sleep
        self._clock: Callable[[], float] = clock

    async def get(self, task_id: str, *, wait_seconds: float = 0) -> TaskReport:
        """The task, after waiting up to ``wait_seconds`` (at most ``MAX_WAIT_SECONDS``) for it.

        The wait ends as soon as the task has ended, successfully or not.

        Raises:
            NotFound: No task with that id exists that the user may see.
            PermissionDenied: The user may not view tasks.
        """
        bound = min(max(wait_seconds, 0), MAX_WAIT_SECONDS)
        start = self._clock()
        status = await self._reader.get_task(task_id)
        while not status.is_final and (left := bound - (self._clock() - start)) > 0:
            await self._sleep(min(POLL_SECONDS, left))
            status = await self._reader.get_task(task_id)
        waited = self._clock() - start
        outcome: Wait = "not_requested"
        if bound > 0:
            outcome = "finished" if status.is_final else "timed_out"
        return _report(status, outcome, waited)


def _report(status: TaskStatus, wait: Wait, waited_seconds: float) -> TaskReport:
    return TaskReport(
        task_id=status.task_id,
        task_type=status.task_type,
        state=status.state,
        message=status.message,
        document_id=status.document_id,
        created=status.created,
        finished=status.finished,
        wait=wait,
        waited_seconds=waited_seconds,
    )
