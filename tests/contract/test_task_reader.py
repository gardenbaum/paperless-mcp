"""Contract of TaskReader: the same assertions against the fake and live Paperless 3.4.2.

Task ids are Celery UUIDs; the backend's `consume` hands out the id of a finished consumption
task (live: a real upload of a generated PDF, whose document is deleted again).
"""

import uuid
from typing import TYPE_CHECKING

import pytest

from paperless_mcp.domain.errors import NotFound, PermissionDenied

if TYPE_CHECKING:
    from contract.conftest import Backend

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-8")]

CONTRACT = "TaskReader.get_task"


@pytest.mark.contract(CONTRACT)
async def test_a_consumed_upload_is_a_finished_task_with_its_document(backend: Backend) -> None:
    task_id = backend.consume(succeed=True)

    task = await backend.admin.tasks.get_task(task_id)

    assert (task.task_id, task.task_type, task.state) == (task_id, "consume_file", "success")
    assert task.is_final
    assert task.document_id is not None
    assert task.message is None
    assert task.finished is not None
    assert task.finished >= task.created


@pytest.mark.contract(CONTRACT)
async def test_a_failed_upload_reports_a_message_and_no_document(backend: Backend) -> None:
    task_id = backend.consume(succeed=False)

    task = await backend.admin.tasks.get_task(task_id)

    assert task.state == "failure"
    assert task.is_final
    assert task.document_id is None
    assert task.message
    assert "Traceback" not in task.message
    assert 'File "' not in task.message


@pytest.mark.contract(CONTRACT)
async def test_an_unknown_task_id_is_not_found(backend: Backend) -> None:
    unknown = str(uuid.uuid4())

    with pytest.raises(NotFound) as raised:
        _ = await backend.admin.tasks.get_task(unknown)

    assert unknown in raised.value.message


@pytest.mark.contract(CONTRACT)
async def test_a_user_who_may_not_view_tasks_is_denied(backend: Backend) -> None:
    task_id = backend.consume(succeed=True)

    with pytest.raises(PermissionDenied):
        _ = await backend.reader.tasks.get_task(task_id)
