"""TaskService.get: one read, or a bounded wait that polls on an injected clock and sleeper."""

from datetime import UTC, datetime

import pytest
from fakes.accounts import ADMIN
from fakes.paperless import FakePaperless, FakeTask, seeded

from paperless_mcp.domain.errors import NotFound
from paperless_mcp.domain.tasks import TaskStatus  # noqa: TC001 -- return annotation
from paperless_mcp.services.tasks import MAX_WAIT_SECONDS, POLL_SECONDS, TaskService

pytestmark = pytest.mark.anyio

ID = "11111111-2222-3333-4444-555555555555"


class ManualClock:
    """A clock and sleeper that never waits: sleeping moves time on, and records the delay."""

    def __init__(self) -> None:
        self.now: float = 1000.0
        self.delays: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.delays.append(delay)
        self.now += delay


class Scenario:
    """A task that starts running and ends `after` seconds of clock time later."""

    def __init__(self, *, after: float | None, ends: str = "success") -> None:
        self.clock: ManualClock = ManualClock()
        self.fake: FakePaperless = FakePaperless(seeded(), user=ADMIN[0])
        self.task: FakeTask = FakeTask(id=1, task_id=ID, status="started")
        self.fake.state.tasks.append(self.task)
        self.started: float = self.clock.now
        self.after: float | None = after
        self.ends: str = ends
        self.reads: int = 0
        self.service: TaskService = TaskService(
            _Reader(self), sleep=self.clock.sleep, clock=self.clock
        )

    def settle(self) -> None:
        """Called on every read: the task has ended once enough time has passed."""
        self.reads += 1
        if self.after is not None and self.clock.now - self.started >= self.after:
            self.task.status = "failure" if self.ends == "failure" else "success"
            self.task.finished = datetime(2026, 11, 12, tzinfo=UTC)
            self.task.document_ids = () if self.ends == "failure" else (9,)
            self.task.message = "broken" if self.ends == "failure" else None


class _Reader:
    def __init__(self, scenario: Scenario) -> None:
        self._scenario: Scenario = scenario

    async def get_task(self, task_id: str) -> TaskStatus:
        self._scenario.settle()
        return await self._scenario.fake.get_task(task_id)


async def test_without_a_wait_the_task_is_read_once_and_never_slept_for() -> None:
    scenario = Scenario(after=None)

    report = await scenario.service.get(ID)

    assert (report.state, report.wait, report.waited_seconds) == ("started", "not_requested", 0.0)
    assert (scenario.reads, scenario.clock.delays) == (1, [])


async def test_a_finished_task_read_without_a_wait_is_still_not_waited_for() -> None:
    scenario = Scenario(after=0)

    report = await scenario.service.get(ID)

    assert (report.state, report.wait, scenario.clock.delays) == ("success", "not_requested", [])


async def test_a_task_that_has_ended_is_reported_at_once_even_when_asked_to_wait() -> None:
    scenario = Scenario(after=0)

    report = await scenario.service.get(ID, wait_seconds=20)

    assert (report.state, report.document_ids) == ("success", (9,))
    assert (report.wait, report.waited_seconds) == ("finished", 0.0)
    assert scenario.clock.delays == []


async def test_waiting_polls_until_the_task_ends_and_reports_the_time_waited() -> None:
    scenario = Scenario(after=5)

    report = await scenario.service.get(ID, wait_seconds=20)

    assert report.state == "success"
    assert (report.wait, report.waited_seconds) == ("finished", 6.0)
    assert scenario.clock.delays == [POLL_SECONDS] * 3
    assert scenario.reads == 4


async def test_a_failed_task_ends_the_wait_with_its_message() -> None:
    scenario = Scenario(after=1, ends="failure")

    report = await scenario.service.get(ID, wait_seconds=20)

    assert (report.state, report.message, report.wait) == ("failure", "broken", "finished")


async def test_a_task_still_running_at_the_bound_is_reported_as_timed_out() -> None:
    scenario = Scenario(after=None)

    report = await scenario.service.get(ID, wait_seconds=10)

    assert (report.state, report.wait, report.waited_seconds) == ("started", "timed_out", 10.0)
    assert scenario.clock.delays == [POLL_SECONDS] * 5


async def test_the_last_sleep_is_cut_to_the_time_that_is_left() -> None:
    scenario = Scenario(after=None)

    report = await scenario.service.get(ID, wait_seconds=5)

    assert scenario.clock.delays == [2.0, 2.0, 1.0]
    assert report.waited_seconds == 5.0


async def test_a_wait_beyond_the_maximum_is_cut_to_the_maximum() -> None:
    scenario = Scenario(after=None)

    report = await scenario.service.get(ID, wait_seconds=10_000)

    assert (report.wait, report.waited_seconds) == ("timed_out", float(MAX_WAIT_SECONDS))
    assert sum(scenario.clock.delays) == MAX_WAIT_SECONDS


class SlowReader:
    """Every read takes three seconds of clock time and finds the task still running."""

    def __init__(self, clock: ManualClock) -> None:
        self.clock: ManualClock = clock
        self.reads: int = 0

    async def get_task(self, task_id: str) -> TaskStatus:
        self.reads += 1
        self.clock.now += 3
        return await FakePaperless(_running(), user=ADMIN[0]).get_task(task_id)


def _running():  # noqa: ANN202 -- test state
    state = seeded()
    state.tasks.append(FakeTask(id=1, task_id=ID, status="started"))
    return state


async def test_slow_reads_cannot_stretch_the_wait_beyond_its_bound() -> None:
    clock = ManualClock()
    reader = SlowReader(clock)
    service = TaskService(reader, sleep=clock.sleep, clock=clock)

    report = await service.get(ID, wait_seconds=5)

    assert (report.state, report.wait) == ("started", "timed_out")
    assert report.waited_seconds == 5.0
    assert reader.reads == 1


async def test_a_wait_over_the_maximum_is_clamped_not_rejected() -> None:
    scenario = Scenario(after=None)

    report = await scenario.service.get(ID, wait_seconds=MAX_WAIT_SECONDS + 1)

    assert report.waited_seconds == float(MAX_WAIT_SECONDS)


async def test_waited_seconds_are_rounded_to_a_tenth() -> None:
    scenario = Scenario(after=None)
    scenario.clock.now += 0  # keep the start
    service = TaskService(_Reader(scenario), sleep=scenario.clock.sleep, clock=scenario.clock)

    report = await service.get(ID, wait_seconds=0.25)

    assert report.waited_seconds == 0.2


async def test_a_negative_wait_is_no_wait() -> None:
    scenario = Scenario(after=None)

    report = await scenario.service.get(ID, wait_seconds=-5)

    assert (report.wait, scenario.clock.delays) == ("not_requested", [])


async def test_an_unknown_task_is_not_found_without_waiting() -> None:
    scenario = Scenario(after=None)

    with pytest.raises(NotFound):
        _ = await scenario.service.get("no-such-task", wait_seconds=20)

    assert scenario.clock.delays == []


# --- Mutation-testing additions (PLMCP-21)


class TickingClock:
    """A clock that moves on with every reading, like real time."""

    def __init__(self) -> None:
        self.now: float = 1000.0

    def __call__(self) -> float:
        self.now += 0.5
        return self.now

    async def sleep(self, delay: float) -> None:
        self.now += delay


async def test_no_wait_reports_no_time_waited_however_long_the_read_took() -> None:
    scenario = Scenario(after=None)
    ticking = TickingClock()
    service = TaskService(_Reader(scenario), sleep=ticking.sleep, clock=ticking)

    report = await service.get(ID)

    assert (report.wait, report.waited_seconds) == ("not_requested", 0.0)


async def test_a_wait_of_one_second_is_a_wait() -> None:
    scenario = Scenario(after=None)

    report = await scenario.service.get(ID, wait_seconds=1)

    assert (report.wait, report.waited_seconds) == ("timed_out", 1.0)


async def test_the_finish_time_of_a_task_is_reported() -> None:
    scenario = Scenario(after=0)

    report = await scenario.service.get(ID, wait_seconds=5)

    assert report.finished == datetime(2026, 11, 12, tzinfo=UTC)
