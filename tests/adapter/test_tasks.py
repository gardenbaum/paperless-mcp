"""PaperlessTaskReader on responses recorded from the Paperless 3.4.2 E2E stack."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx2
import pytest

from paperless_mcp.domain.errors import NotFound, PermissionDenied
from paperless_mcp.domain.tasks import TaskStatus
from paperless_mcp.paperless.tasks import PaperlessTaskReader

if TYPE_CHECKING:
    from adapter.conftest import ClientFactory

pytestmark = pytest.mark.anyio

type Json = dict[str, object]

UUID = "e52f7466-0b2a-4106-86af-9fda489a6c52"
DONE: Json = {
    "id": 5,
    "task_id": UUID,
    "task_type": "consume_file",
    "task_type_display": "Consume File",
    "trigger_source": "api_upload",
    "trigger_source_display": "API Upload",
    "status": "success",
    "status_display": "Success",
    "date_created": "2026-11-12T17:47:20.279846Z",
    "date_started": "2026-11-12T17:47:20.290881Z",
    "date_done": "2026-11-12T17:47:26.284742Z",
    "duration_seconds": 5.993861,
    "wait_time_seconds": 0.011035,
    "input_data": {"filename": "t.pdf", "mime_type": "application/pdf"},
    "result_data": {"document_id": 3},
    "related_document_ids": [3],
    "acknowledged": False,
    "owner": 2,
}
RUNNING: Json = {
    **DONE,
    "status": "started",
    "status_display": "Started",
    "date_done": None,
    "duration_seconds": None,
    "wait_time_seconds": None,
    "result_data": None,
    "related_document_ids": [],
}
FAILED: Json = {
    **DONE,
    "status": "failure",
    "status_display": "Failure",
    "result_data": {
        "error_type": "ConsumerError",
        "error_message": "bad.pdf: Error occurred while consuming: InputFileError: ",
        "traceback": '  File "/usr/src/paperless/src/documents/consumer.py", line 577, in run\n',
    },
    "related_document_ids": [],
}


class Tasks:
    """Answers GET /api/tasks/ with `rows` and records the query parameters sent."""

    def __init__(self, *rows: Json) -> None:
        self.rows: list[Json] = list(rows)
        self.sent: list[dict[str, str]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.sent.append(dict(request.url.params))
        body = {"count": len(self.rows), "next": None, "previous": None, "results": self.rows}
        return httpx2.Response(200, json=body)


async def test_a_task_is_looked_up_by_its_celery_id_and_mapped(make_client: ClientFactory) -> None:
    server = Tasks(DONE)

    status = await PaperlessTaskReader(make_client(server)).get_task(UUID)

    assert server.sent == [{"task_id": UUID}]
    assert status == TaskStatus(
        task_id=UUID,
        task_type="consume_file",
        state="success",
        message=None,
        document_ids=(3,),
        created=datetime(2026, 11, 12, 17, 47, 20, 279846, tzinfo=UTC),
        finished=datetime(2026, 11, 12, 17, 47, 26, 284742, tzinfo=UTC),
    )


async def test_a_running_task_has_no_document_and_is_not_finished(
    make_client: ClientFactory,
) -> None:
    status = await PaperlessTaskReader(make_client(Tasks(RUNNING))).get_task(UUID)

    assert (status.state, status.document_ids, status.finished) == ("started", (), None)


async def test_a_failed_task_reports_the_error_message_never_the_traceback(
    make_client: ClientFactory,
) -> None:
    status = await PaperlessTaskReader(make_client(Tasks(FAILED))).get_task(UUID)

    assert status.state == "failure"
    assert status.message == ("bad.pdf: Error occurred while consuming: InputFileError:")
    assert "consumer.py" not in status.model_dump_json()


async def test_a_task_that_wrote_several_documents_reports_them_all(
    make_client: ClientFactory,
) -> None:
    row: Json = {**DONE, "related_document_ids": [7, 8]}

    status = await PaperlessTaskReader(make_client(Tasks(row))).get_task(UUID)

    assert status.document_ids == (7, 8)


async def test_no_row_for_the_id_is_not_found(make_client: ClientFactory) -> None:
    with pytest.raises(NotFound) as raised:
        _ = await PaperlessTaskReader(make_client(Tasks())).get_task("nope")

    assert "nope" in raised.value.message
    assert raised.value.hint


async def test_a_user_without_the_task_permission_is_denied(make_client: ClientFactory) -> None:
    def forbidden(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            403, json={"detail": "You do not have permission to perform this action."}
        )

    with pytest.raises(PermissionDenied):
        _ = await PaperlessTaskReader(make_client(forbidden)).get_task(UUID)


@pytest.mark.parametrize("asked", ["other", "E52F7466-0B2A-4106-86AF-9FDA489A6C52"])
async def test_a_row_for_another_task_is_never_taken_for_the_one_asked_for(
    make_client: ClientFactory, asked: str
) -> None:
    with pytest.raises(NotFound):
        _ = await PaperlessTaskReader(make_client(Tasks(DONE))).get_task(asked)


@pytest.mark.parametrize("blank", ["", "  "])
async def test_a_blank_id_is_not_found_without_asking_paperless(
    make_client: ClientFactory, blank: str
) -> None:
    server = Tasks(DONE)

    with pytest.raises(NotFound):
        _ = await PaperlessTaskReader(make_client(server)).get_task(blank)

    assert server.sent == []


# --- Mutation-testing additions (PLMCP-21)


@pytest.mark.parametrize("asked", ["nope", "  "])
async def test_the_not_found_error_names_the_id_asked_for_and_says_where_ids_come_from(
    make_client: ClientFactory, asked: str
) -> None:
    with pytest.raises(NotFound) as raised:
        _ = await PaperlessTaskReader(make_client(Tasks())).get_task(asked)

    assert raised.value.message == f"No task {asked} exists that this user may see."
    assert raised.value.hint == "Use the task id an upload returned; it is a UUID."
