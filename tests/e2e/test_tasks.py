"""get_task through the assembled server against the seeded Paperless 3.4.2 stack."""

import time
import uuid
from typing import TYPE_CHECKING, cast

import pytest
from fastmcp import Client
from harness import paperless_settings, server_settings

from e2e.helpers import CONSUME_TIMEOUT, num, obj, parse, pdf, results, seq
from paperless_mcp.server import create_server

if TYPE_CHECKING:
    from collections.abc import Iterator

    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]

type Json = dict[str, object]


async def get_task(stack: Stack, token: str, **arguments: object) -> Json:
    server = create_server(paperless_settings(stack.base_url, token), server_settings())
    async with Client(server) as client:
        result = await client.call_tool("get_task", arguments)
    assert result.structured_content is not None
    return result.structured_content


@pytest.fixture
def upload(stack: Stack) -> Iterator[str]:
    """The task id of a PDF just posted for consumption.

    Teardown waits for the task to end (also when the test did not), then deletes its document.
    """
    title = f"e2e-task-{uuid.uuid4().hex}"
    posted = stack.admin.post(
        "/api/documents/post_document/",
        data={"title": title},
        files={"document": (f"{title}.pdf", pdf(title), "application/pdf")},
    ).raise_for_status()
    task_id = str(parse(posted))
    yield task_id
    deadline = time.monotonic() + CONSUME_TIMEOUT
    while time.monotonic() < deadline:
        [task] = results(stack.admin.get("/api/tasks/", params={"task_id": task_id}))
        if task["status"] in {"success", "failure", "revoked"}:
            for document in seq(task["related_document_ids"]):
                _ = stack.admin.delete(f"/api/documents/{num(document)}/")
            return
        time.sleep(1)


async def test_waiting_follows_a_fresh_upload_to_its_document(stack: Stack, upload: str) -> None:
    output = await get_task(stack, stack.admin_token, task_id=upload, wait_seconds=30)

    assert (output["state"], output["wait"]) == ("success", "finished")
    assert output["task_type"] == "consume_file"
    [document_id] = cast("list[int]", output["document_ids"])
    document = obj(parse(stack.admin.get(f"/api/documents/{document_id}/")))
    assert str(document["title"]).startswith("e2e-task-")


async def test_an_unknown_task_id_is_a_tool_error(stack: Stack) -> None:
    server = create_server(paperless_settings(stack.base_url, stack.admin_token), server_settings())
    async with Client(server) as client:
        result = await client.call_tool(
            "get_task", {"task_id": str(uuid.uuid4())}, raise_on_error=False
        )

    assert result.is_error
    assert "No task" in str(result.content)


async def test_a_user_without_the_task_permission_gets_a_tool_error(
    stack: Stack, upload: str
) -> None:
    server = create_server(
        paperless_settings(stack.base_url, stack.reader_token), server_settings()
    )
    async with Client(server) as client:
        result = await client.call_tool("get_task", {"task_id": upload}, raise_on_error=False)

    assert result.is_error
    assert "may not" in str(result.content)
