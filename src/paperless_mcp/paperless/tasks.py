"""TaskReader on ``GET /api/tasks/?task_id=``.

``/api/tasks/{id}/`` takes the database id, but clients hold the Celery UUID Paperless returns
when it accepts an upload, so the list filter ``task_id`` is the lookup. An unknown id is an
empty list, not a 404. A failed task carries its traceback in ``result_data``; only the error
message is taken from it.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, JsonValue

from paperless_mcp.domain.errors import NotFound
from paperless_mcp.domain.tasks import TaskState, TaskStatus
from paperless_mcp.paperless.pagination import Page

if TYPE_CHECKING:
    from paperless_mcp.paperless.client import PaperlessClient

PATH = "/api/tasks/"


class _Row(BaseModel):
    task_id: str
    task_type: str
    status: TaskState
    date_created: datetime
    date_done: datetime | None
    result_data: JsonValue
    related_document_ids: list[int]


def not_found(task_id: str) -> NotFound:
    """The error for a task id Paperless does not know (or hides from the user)."""
    return NotFound(
        f"No task {task_id} exists that this user may see.",
        hint="Use the task id an upload returned; it is a UUID.",
    )


def _error_message(result: JsonValue) -> str | None:
    if not isinstance(result, dict):
        return None
    message = result.get("error_message")
    return message.strip() if isinstance(message, str) and message.strip() else None


class PaperlessTaskReader:
    """``TaskReader`` over the Paperless REST API."""

    def __init__(self, client: PaperlessClient) -> None:
        """Read through ``client``."""
        self._client: PaperlessClient = client

    async def get_task(self, task_id: str) -> TaskStatus:
        """See ``TaskReader.get_task``."""
        found = await self._client.get(PATH, Page[_Row], params={"task_id": task_id})
        if not found.results:
            raise not_found(task_id)
        row = found.results[0]
        return TaskStatus(
            task_id=row.task_id,
            task_type=row.task_type,
            state=row.status,
            message=_error_message(row.result_data),
            document_id=row.related_document_ids[0] if row.related_document_ids else None,
            created=row.date_created,
            finished=row.date_done,
        )
