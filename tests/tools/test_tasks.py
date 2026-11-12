"""get_task through the in-memory client on the seeded fake."""

from typing import override

import pytest
from fakes.paperless import FakePaperless, FakeTask, seeded
from fastmcp import Client
from harness import admin, serve
from services.test_tasks import ManualClock

from paperless_mcp.services.tasks import MAX_WAIT_SECONDS

pytestmark = pytest.mark.anyio

type Json = dict[str, object]

ID = "11111111-2222-3333-4444-555555555555"


def with_task(**fields: object) -> FakePaperless:
    fake = admin()
    fake.state.tasks.append(FakeTask(id=1, task_id=ID, **fields))  # pyright: ignore[reportArgumentType] -- test fields
    return fake


async def call(fake: FakePaperless, arguments: Json, clock: ManualClock | None = None) -> Json:
    clock = clock or ManualClock()
    server = serve(fake, sleep=clock.sleep, clock=clock)
    async with Client(server) as client:
        result = await client.call_tool("get_task", arguments)
    assert result.structured_content is not None
    return result.structured_content


async def test_a_finished_upload_reports_state_and_document() -> None:
    output = await call(
        with_task(status="success", document_id=7, task_type="consume_file"), {"task_id": ID}
    )

    assert output["state"] == "success"
    assert output["document_id"] == 7
    assert output["wait"] == "not_requested"
    assert output["message"] is None


async def test_wait_seconds_polls_until_the_task_ends_without_really_sleeping() -> None:
    fake = with_task(status="started")

    class Finishing(ManualClock):
        @override
        async def sleep(self, delay: float) -> None:
            await super().sleep(delay)
            fake.state.tasks[0].status = "failure"
            fake.state.tasks[0].message = "broken.pdf: could not be read"

    output = await call(fake, {"task_id": ID, "wait_seconds": 20}, Finishing())

    assert (output["state"], output["wait"]) == ("failure", "finished")
    assert output["message"] == "broken.pdf: could not be read"
    assert output["waited_seconds"] == 2.0


async def test_a_task_still_running_after_the_wait_says_so() -> None:
    clock = ManualClock()

    output = await call(with_task(status="pending"), {"task_id": ID, "wait_seconds": 6}, clock)

    assert (output["state"], output["wait"], output["waited_seconds"]) == (
        "pending",
        "timed_out",
        6.0,
    )


async def test_an_unknown_task_is_a_tool_error_with_a_hint() -> None:
    async with Client(serve(admin())) as client:
        result = await client.call_tool("get_task", {"task_id": "nope"}, raise_on_error=False)

    assert result.is_error
    assert "No task nope exists" in str(result.content)
    assert "Hint:" in str(result.content)


@pytest.mark.parametrize("wait", [-1, MAX_WAIT_SECONDS + 1])
async def test_a_wait_outside_the_bounds_is_rejected(wait: int) -> None:
    async with Client(serve(with_task(status="started"))) as client:
        result = await client.call_tool(
            "get_task", {"task_id": ID, "wait_seconds": wait}, raise_on_error=False
        )

    assert result.is_error


async def test_a_user_who_may_not_view_tasks_gets_a_tool_error() -> None:
    state = seeded()
    state.tasks.append(FakeTask(id=1, task_id=ID))
    async with Client(serve(FakePaperless(state, user="e2e-reader"))) as client:
        result = await client.call_tool("get_task", {"task_id": ID}, raise_on_error=False)

    assert result.is_error
    assert "may not" in str(result.content)
