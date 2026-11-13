"""What became of a file Paperless accepted: its consumption task, followed for a bounded time.

Shared by the uploads of documents and of versions. The file is in Paperless by the time the task
is read, so nothing that goes wrong here may be raised: an error would invite a retry, and with it
a second document or version. The task id always gets out; a task that cannot be read is
reported as ``unknown``.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from paperless_mcp.domain.errors import PaperlessMcpError

if TYPE_CHECKING:
    from paperless_mcp.domain.documents import UploadState
    from paperless_mcp.domain.tasks import Wait
    from paperless_mcp.services.tasks import TaskService


@dataclass(frozen=True, slots=True)
class Outcome:
    """The state of a consumption task.

    Attributes:
        document_id: The document the task names once it succeeded (for a version upload, the
            new version); None while it runs, after a failure, or when it names none.
        document_ids: All the documents the task names once it succeeded.
    """

    state: UploadState
    message: str | None
    document_id: int | None
    document_ids: tuple[int, ...]
    wait: Wait
    waited_seconds: float


async def follow(
    tasks: TaskService,
    task_id: str,
    wait_seconds: float,
    *,
    nothing_named: str = "",
    accepted: str = "the file",
) -> Outcome:
    """The outcome of task ``task_id`` after waiting up to ``wait_seconds`` for it.

    ``nothing_named`` explains a task that succeeded without naming a document (by default it
    needs no explanation); ``accepted`` names what Paperless took, for the message of a task that
    cannot be read.
    """
    try:
        report = await tasks.get(task_id, wait_seconds=wait_seconds)
        named = report.document_ids if report.state == "success" else ()
        document = named[0] if named else None
    except Exception as error:  # noqa: BLE001 -- the file is in Paperless: the task id must get out
        reason = error.message if isinstance(error, PaperlessMcpError) else "unexpected error"
        return Outcome(
            state="unknown",
            message=(
                f"Paperless accepted {accepted}, but its task could not be read ({reason}); "
                "the outcome is unknown. Follow the task with get_task."
            ),
            document_id=None,
            document_ids=(),
            wait="timed_out" if wait_seconds > 0 else "not_requested",
            waited_seconds=0,
        )
    note = nothing_named if report.state == "success" and document is None else None
    return Outcome(
        state=report.state,
        message=report.message or note or None,
        document_id=document,
        document_ids=named,
        wait=report.wait,
        waited_seconds=report.waited_seconds,
    )
