"""PdfService: PDF operations behind their checks and, where pages or originals go, confirm.

The fakes of the "refused" cases are created without `allow_writes`: the fake's write guard raises
`WriteNotAllowedError` on any write, so a passing test proves nothing was sent to Paperless.
"""

from dataclasses import replace
from typing import TYPE_CHECKING, final, override

import pytest
from fakes.accounts import ADMIN
from fakes.paperless import FakeDocument, FakePaperless, FakeState, seeded

from paperless_mcp.domain.errors import (
    CONFIRM_HINT,
    ConfirmationRequired,
    NotFound,
    PermissionDenied,
    UpstreamTimeout,
    Validation,
)
from paperless_mcp.domain.pdf import PageSlot
from paperless_mcp.services.pdf import MAX_DOCUMENTS, PdfService
from paperless_mcp.services.tasks import TaskService

if TYPE_CHECKING:
    from collections.abc import Awaitable, Sequence

    from paperless_mcp.domain.pdf import Output
    from paperless_mcp.domain.tasks import TaskStatus

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-6")]

EDITING = frozenset({"view_document", "change_document", "add_document", "delete_document"})


class Clock:
    def __init__(self) -> None:
        self.now: float = 0.0
        self.step: float = 0.0

    def __call__(self) -> float:
        self.now += self.step
        return self.now

    async def sleep(self, delay: float) -> None:
        self.now += delay


class Slow(FakePaperless):
    """The fake, with tasks that stay `started` for the first reads and can fail or not be read."""

    def __init__(self, state: FakeState, *, user: str, allow_writes: bool, slow_reads: int) -> None:
        super().__init__(state, user=user, allow_writes=allow_writes)
        self.slow_reads: int = slow_reads
        self.reads: int = 0
        self.read_error: Exception | None = None
        self.failing: str | None = None
        self.keep_documents: bool = False

    @override
    async def get_task(self, task_id: str) -> TaskStatus:
        self.reads += 1
        if self.read_error:
            raise self.read_error
        status = await super().get_task(task_id)
        if self.failing:
            update: dict[str, object] = {"state": "failure", "message": self.failing}
            if not self.keep_documents:
                update["document_ids"] = ()
            return status.model_copy(update=update)
        if self.reads <= self.slow_reads:
            return status.model_copy(
                update={"state": "started", "document_ids": (), "finished": None}
            )
        return status


@final
class Setup:
    def __init__(
        self,
        *,
        writes: bool = True,
        permissions: frozenset[str] | None = None,
        slow_reads: int = 0,
    ) -> None:
        self.state: FakeState = seeded()
        user = ADMIN[0]
        if permissions is not None:
            self.state.users = [
                replace(u, is_superuser=False, permissions=permissions) if u.username == user else u
                for u in self.state.users
            ]
        self.fake: Slow = Slow(self.state, user=user, allow_writes=writes, slow_reads=slow_reads)
        self.clock: Clock = Clock()
        self.service: PdfService = PdfService(
            self.fake, self.fake, TaskService(self.fake, sleep=self.clock.sleep, clock=self.clock)
        )

    def pdf(self, pages: int, title: str = "Report", **fields: object) -> int:
        number = max(d.id for d in self.state.documents) + 1
        self.state.documents.append(
            FakeDocument(id=number, title=title, owner=2, page_count=pages, **fields)  # pyright: ignore[reportArgumentType] -- test fields
        )
        return number

    def document(self, number: int) -> FakeDocument:
        [found] = [d for d in self.state.documents if d.id == number]
        return found

    def untouched(self) -> bool:
        return not self.state.tasks and not self.state.trash


def trashed(setup: Setup) -> set[int]:
    return {d.id for d in setup.state.trash}


# --- rotate -------------------------------------------------------------------------------


async def test_rotating_returns_the_finished_tasks_with_the_new_versions() -> None:
    setup = Setup()
    first, second = setup.pdf(2, "One"), setup.pdf(3, "Two")

    result = await setup.service.rotate([first, second], 90, wait_seconds=5)

    assert (result.operation, result.state, result.source_document_ids) == (
        "rotate",
        "success",
        (first, second),
    )
    assert result.summary == "rotate 2 documents by 90 degrees clockwise"
    assert len(result.tasks) == 2
    assert all(t.state == "success" for t in result.tasks)
    versions = [setup.document(n).versions[0].id for n in (first, second)]
    assert result.produced_ids == tuple(versions)
    assert result.message is None
    assert result.wait == "finished"


async def test_rotating_one_document_by_270_says_so_in_the_summary() -> None:
    setup = Setup()

    result = await setup.service.rotate([setup.pdf(1)], 270)

    assert result.summary == "rotate 1 document by 270 degrees clockwise"
    assert (result.wait, result.waited_seconds) == ("not_requested", 0.0)


@pytest.mark.parametrize("degrees", [0, 360, 45, -45])
async def test_a_rotation_that_is_not_a_quarter_turn_is_refused(degrees: int) -> None:
    setup = Setup(writes=False)

    with pytest.raises(Validation, match="quarter turn"):
        _ = await setup.service.rotate([setup.pdf(1)], degrees)


async def test_a_negative_quarter_turn_is_sent_as_the_clockwise_rotation_it_equals() -> None:
    setup = Setup()

    result = await setup.service.rotate([setup.pdf(1)], -90)

    assert result.summary == "rotate 1 document by 270 degrees clockwise"


@pytest.mark.parametrize(
    ("ids", "message"),
    [
        ([], "at least one document"),
        ([3, 3], "listed twice"),
        (list(range(100, 100 + MAX_DOCUMENTS + 1)), f"at most {MAX_DOCUMENTS}"),
    ],
)
async def test_a_selection_of_the_wrong_size_or_with_repeats_is_refused(
    ids: list[int], message: str
) -> None:
    setup = Setup(writes=False)

    with pytest.raises(Validation, match=message):
        _ = await setup.service.rotate(ids, 90)

    assert setup.untouched()


async def test_a_document_that_is_not_a_pdf_is_refused_with_its_type() -> None:
    setup = Setup(writes=False)
    text = setup.pdf(1, "Notes", mime_type="text/plain")

    with pytest.raises(Validation, match=rf"Document {text} is not a PDF \(text/plain\)"):
        _ = await setup.service.rotate([text], 90)


async def test_a_missing_document_is_not_found_and_a_read_only_one_is_refused() -> None:
    setup = Setup(writes=False)
    locked = setup.pdf(1)
    setup.document(locked).owner = 3  # the reader's; the admin is a superuser, so not refused

    with pytest.raises(NotFound):
        _ = await setup.service.rotate([999], 90)
    other = Setup(writes=False, permissions=frozenset({"view_document", "change_document"}))
    foreign = other.pdf(1)
    other.document(foreign).owner = 3
    other.document(foreign).viewers = {2}
    with pytest.raises(PermissionDenied, match=f"may not change document {foreign}"):
        _ = await other.service.rotate([foreign], 90)


async def test_a_user_who_may_not_view_tasks_gets_an_unknown_state_that_says_why() -> None:
    setup = Setup(permissions=EDITING)

    result = await setup.service.rotate([setup.pdf(1)], 90, wait_seconds=5)

    assert (result.state, result.tasks, result.produced_ids) == ("unknown", (), ())
    assert result.message is not None
    assert "no task" in result.message
    assert "may not view tasks" in result.message
    assert (result.wait, result.waited_seconds) == ("not_requested", 0.0)


# --- waiting and the state of the tasks ----------------------------------------------------


async def test_a_wait_that_is_not_enough_reports_the_tasks_as_still_running() -> None:
    setup = Setup(slow_reads=1000)
    first, second = setup.pdf(1), setup.pdf(1)

    result = await setup.service.rotate([first, second], 90, wait_seconds=4)

    assert result.state == "started"
    assert [t.state for t in result.tasks] == ["started", "started"]
    assert result.produced_ids == ()
    assert result.wait == "timed_out"
    assert result.waited_seconds == 4.0  # the bound is shared by the tasks, not paid per task


async def test_the_wait_ends_when_the_tasks_have_ended() -> None:
    setup = Setup(slow_reads=2)
    first, second = setup.pdf(1), setup.pdf(1)

    result = await setup.service.rotate([first, second], 90, wait_seconds=30)

    assert (result.state, result.wait) == ("success", "finished")
    assert 0 < result.waited_seconds < 30


async def test_a_failed_task_makes_the_operation_a_failure_with_its_message() -> None:
    setup = Setup()
    setup.fake.failing = "x.pdf: Error occurred while consuming document"

    result = await setup.service.rotate([setup.pdf(1)], 90, wait_seconds=1)

    assert result.state == "failure"
    assert result.message == "x.pdf: Error occurred while consuming document"
    assert result.produced_ids == ()


async def test_a_task_that_cannot_be_read_makes_the_state_unknown_but_the_ids_get_out() -> None:
    setup = Setup()
    setup.fake.read_error = UpstreamTimeout("Paperless did not respond in time.")

    result = await setup.service.rotate([setup.pdf(1)], 90, wait_seconds=1)

    [task] = result.tasks
    assert (result.state, task.state) == ("unknown", "unknown")
    assert result.message is not None
    assert "accepted the request" in result.message
    assert "Paperless did not respond in time" in result.message


# --- merge --------------------------------------------------------------------------------


async def test_merging_makes_one_new_document_and_keeps_the_originals() -> None:
    setup = Setup()
    first, second = setup.pdf(2, "A"), setup.pdf(3, "B")

    result = await setup.service.merge([first, second], second, wait_seconds=5)

    [merged] = result.produced_ids
    assert setup.document(merged).page_count == 5
    assert setup.document(merged).title == "B (merged)"
    assert result.summary == f"merge documents {first} and {second} into one new document"
    assert trashed(setup) == set()


async def test_merging_needs_at_least_two_documents_and_a_metadata_document_among_them() -> None:
    setup = Setup(writes=False)
    first, second, third = setup.pdf(1), setup.pdf(1), setup.pdf(1)

    with pytest.raises(Validation, match="at least two"):
        _ = await setup.service.merge([first], None)
    with pytest.raises(Validation, match=f"Document {third} is not among"):
        _ = await setup.service.merge([first, second], third)


async def test_deleting_the_originals_of_a_merge_needs_confirm_and_says_what_goes() -> None:
    setup = Setup(writes=False)
    first, second = setup.pdf(1, "Alpha"), setup.pdf(1, "Beta")

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.merge([first, second], None, delete_originals=True)

    message = raised.value.message
    assert message.startswith("Nothing was changed. Confirming would merge ")
    assert f"document {first} 'Alpha'" in message
    assert f"document {second} 'Beta'" in message
    assert "move the originals to the Paperless trash" in message
    assert raised.value.hint == CONFIRM_HINT
    assert setup.untouched()


async def test_a_confirmed_merge_moves_the_originals_to_the_trash() -> None:
    setup = Setup()
    first, second = setup.pdf(1), setup.pdf(1)

    result = await setup.service.merge([first, second], None, delete_originals=True, confirm=True)

    assert trashed(setup) == {first, second}
    assert result.summary == (
        f"merge documents {first} and {second} into one new document and move the originals to "
        "the Paperless trash"
    )


# --- split --------------------------------------------------------------------------------


async def test_splitting_makes_a_document_per_part_and_keeps_the_original() -> None:
    setup = Setup()
    document = setup.pdf(5, "Scan")

    result = await setup.service.split(document, ["1-2", "3,5"], wait_seconds=5)

    counts = sorted(setup.document(n).page_count or 0 for n in result.produced_ids)
    assert counts == [2, 2]
    assert result.summary == (
        f"split document {document} into 2 new documents: pages 1-2 | pages 3 and 5"
    )
    assert trashed(setup) == set()


async def test_the_pages_left_out_of_a_split_are_not_in_any_part() -> None:
    setup = Setup()
    document = setup.pdf(6)

    result = await setup.service.split(document, ["1", "6"])

    assert len(result.tasks) == 2
    assert setup.state.documents[-1].page_count == 1


@pytest.mark.parametrize(
    ("parts", "message"),
    [
        (["1-2"], "at least two parts"),
        ([], "at least two parts"),
        (["1-2", "2-3"], "Page 2 is in part 1 and part 2"),
        (["1", "9"], "Page 9 does not exist: the document has 4 pages"),
        (["1", "x"], "'x' is not a page or a range"),
        (["1", ""], "No pages given"),
        (["1"] * 21, "at most 20 parts"),
    ],
)
async def test_a_bad_split_is_refused_before_anything_is_sent(
    parts: list[str], message: str
) -> None:
    setup = Setup(writes=False)

    with pytest.raises(Validation, match=message):
        _ = await setup.service.split(setup.pdf(4), parts)

    assert setup.untouched()


async def test_splitting_cannot_make_a_version() -> None:
    setup = Setup(writes=False)

    with pytest.raises(Validation, match="several files"):
        _ = await setup.service.split(setup.pdf(4), ["1", "2"], output="new_version")


async def test_replacing_the_original_with_the_parts_needs_confirm() -> None:
    setup = Setup(writes=False)
    document = setup.pdf(4, "Scan")

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.split(document, ["1-2", "3-4"], output="replace_original")

    assert f"split document {document} 'Scan' into 2 new documents" in raised.value.message
    assert "move the original to the Paperless trash" in raised.value.message
    assert setup.untouched()


async def test_a_confirmed_replacing_split_trashes_the_original() -> None:
    setup = Setup()
    document = setup.pdf(4)

    result = await setup.service.split(
        document, ["1-2", "3-4"], output="replace_original", confirm=True
    )

    assert trashed(setup) == {document}
    assert result.summary.endswith("and move the original to the Paperless trash")


# --- delete pages -------------------------------------------------------------------------


async def test_deleting_pages_needs_confirm_and_says_which_pages_and_what_stays() -> None:
    setup = Setup(writes=False)
    document = setup.pdf(5, "Scan")

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.delete_pages(document, "2-3,5")

    message = raised.value.message
    assert f"delete pages 2-3 and 5 of document {document} 'Scan'" in message
    assert "a new version without them" in message
    assert "earlier version" in message
    assert setup.untouched()


async def test_a_confirmed_page_deletion_makes_a_version_with_the_other_pages() -> None:
    setup = Setup()
    document = setup.pdf(5)

    result = await setup.service.delete_pages(document, "2-3,5", confirm=True, wait_seconds=5)

    assert setup.document(document).page_count == 2
    assert len(setup.document(document).versions) == 2
    assert result.summary == f"delete pages 2-3 and 5 of document {document} as a new version"
    assert len(result.produced_ids) == 1


async def test_deleting_pages_into_a_new_document_needs_no_confirm() -> None:
    setup = Setup()
    document = setup.pdf(3)

    result = await setup.service.delete_pages(document, "1", output="new_document")

    assert [d.page_count for d in setup.state.documents if d.id > document] == [2]
    assert setup.document(document).page_count == 3
    assert result.summary == f"delete page 1 of document {document} into a new document"


async def test_deleting_pages_while_trashing_the_original_needs_confirm() -> None:
    setup = Setup(writes=False)
    document = setup.pdf(3, "Scan")

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.delete_pages(document, "1", output="replace_original")

    assert (
        f"delete page 1 of document {document} 'Scan' into a new document" in raised.value.message
    )
    assert "move the original to the Paperless trash" in raised.value.message


async def test_every_page_cannot_be_deleted_and_bad_ranges_are_refused() -> None:
    setup = Setup(writes=False)
    document = setup.pdf(3)

    with pytest.raises(Validation, match="every page"):
        _ = await setup.service.delete_pages(document, "1-3", confirm=True)
    with pytest.raises(Validation, match="Page 4 does not exist"):
        _ = await setup.service.delete_pages(document, "2-4", confirm=True)
    with pytest.raises(Validation, match="No pages given"):
        _ = await setup.service.delete_pages(document, " ", confirm=True)

    assert setup.untouched()


async def test_page_operations_need_a_known_page_count() -> None:
    setup = Setup(writes=False)
    locked = setup.pdf(1, "Locked", password="x")
    setup.document(locked).page_count = None

    with pytest.raises(Validation, match="page count") as raised:
        _ = await setup.service.delete_pages(locked, "1", confirm=True)

    assert raised.value.hint is not None
    assert "remove_document_password" in raised.value.hint


# --- edit_pdf -----------------------------------------------------------------------------


async def test_an_edit_builds_new_documents_from_the_pages_and_rotations() -> None:
    setup = Setup()
    document = setup.pdf(3, "Scan")
    slots = [PageSlot(page=3), PageSlot(page=1, rotate=90), PageSlot(page=2, document=1)]

    result = await setup.service.edit(document, slots, wait_seconds=5)

    assert sorted(setup.document(n).page_count or 0 for n in result.produced_ids) == [1, 2]
    assert result.summary == (f"edit document {document} into 2 new documents (3 pages, 1 turned)")


async def test_an_edit_as_a_new_version_that_keeps_every_page_needs_no_confirm() -> None:
    setup = Setup()
    document = setup.pdf(2)
    slots = [PageSlot(page=2), PageSlot(page=1, rotate=180)]

    result = await setup.service.edit(document, slots, output="new_version")

    assert len(setup.document(document).versions) == 2
    assert result.summary == f"edit document {document} as a new version (2 pages, 1 turned)"


async def test_an_edit_as_a_new_version_that_drops_pages_needs_confirm() -> None:
    setup = Setup(writes=False)
    document = setup.pdf(4, "Scan")

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.edit(document, [PageSlot(page=1)], output="new_version")

    assert f"drop pages 2-4 of document {document} 'Scan'" in raised.value.message
    assert "new version" in raised.value.message


async def test_a_repeated_page_is_not_a_dropped_one() -> None:
    setup = Setup()
    document = setup.pdf(1)

    result = await setup.service.edit(
        document, [PageSlot(page=1), PageSlot(page=1, rotate=90)], output="new_version"
    )

    assert result.summary.endswith("(2 pages, 1 turned)")
    assert setup.document(document).page_count == 2


async def test_an_edit_that_replaces_the_original_needs_confirm() -> None:
    setup = Setup(writes=False)
    document = setup.pdf(2, "Scan")

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.edit(document, [PageSlot(page=1)], output="replace_original")

    assert f"edit document {document} 'Scan'" in raised.value.message
    assert "move the original to the Paperless trash" in raised.value.message


@pytest.mark.parametrize(
    ("slots", "output", "message"),
    [
        ([], "new_document", "at least one page"),
        ([PageSlot(page=0)], "new_document", "Page numbers start at 1"),
        ([PageSlot(page=3)], "new_document", "Page 3 does not exist: the document has 2 pages"),
        ([PageSlot(page=1, rotate=45)], "new_document", "multiple of 90"),
        ([PageSlot(page=1, document=-1)], "new_document", "Output files are numbered from 0"),
        ([PageSlot(page=1, document=1)], "new_document", "without gaps"),
        (
            [PageSlot(page=1), PageSlot(page=2, document=2)],
            "new_document",
            "without gaps",
        ),
        (
            [PageSlot(page=1), PageSlot(page=2, document=1)],
            "new_version",
            "several files",
        ),
    ],
)
async def test_a_bad_edit_is_refused_before_anything_is_sent(
    slots: list[PageSlot], output: Output, message: str
) -> None:
    setup = Setup(writes=False)

    with pytest.raises(Validation, match=message):
        _ = await setup.service.edit(setup.pdf(2), slots, output=output, confirm=True)

    assert setup.untouched()


async def test_an_edit_may_have_at_most_500_pages() -> None:
    setup = Setup(writes=False)

    with pytest.raises(Validation, match="at most 500 pages"):
        _ = await setup.service.edit(setup.pdf(2), [PageSlot(page=1)] * 501, confirm=True)


# --- remove_password ----------------------------------------------------------------------


async def test_removing_a_password_makes_an_unlocked_copy_and_never_repeats_the_password() -> None:
    setup = Setup()
    locked = setup.pdf(1, "Locked", password="s3cret")
    setup.document(locked).page_count = None

    result = await setup.service.remove_password(locked, "s3cret", wait_seconds=5)

    [copy] = result.produced_ids
    assert setup.document(copy).page_count == 1
    assert result.summary == f"remove the password of document {locked} into a new document"
    assert "s3cret" not in result.model_dump_json()


async def test_a_wrong_password_is_refused() -> None:
    setup = Setup()
    locked = setup.pdf(1, "Locked", password="s3cret")

    with pytest.raises(Validation, match="password"):
        _ = await setup.service.remove_password(locked, "nope")


async def test_a_password_can_be_removed_as_a_new_version_without_confirm() -> None:
    setup = Setup()
    locked = setup.pdf(1, "Locked", password="s3cret")

    result = await setup.service.remove_password(locked, "s3cret", output="new_version")

    assert len(setup.document(locked).versions) == 2
    assert result.summary == f"remove the password of document {locked} as a new version"


async def test_replacing_a_locked_original_needs_confirm() -> None:
    setup = Setup(writes=False)
    locked = setup.pdf(1, "Locked", password="s3cret")

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.remove_password(locked, "s3cret", output="replace_original")

    assert f"remove the password of document {locked} 'Locked'" in raised.value.message
    assert "move the original to the Paperless trash" in raised.value.message
    assert "s3cret" not in raised.value.message


async def test_an_empty_password_is_refused() -> None:
    setup = Setup(writes=False)

    with pytest.raises(Validation, match="password"):
        _ = await setup.service.remove_password(setup.pdf(1), "")


async def test_a_file_without_a_password_says_that_no_task_was_started() -> None:
    setup = Setup()

    result = await setup.service.remove_password(setup.pdf(1), "x")

    assert (result.state, result.tasks) == ("unknown", ())
    assert result.message is not None
    assert "not be password protected" in result.message


# --- reprocess ----------------------------------------------------------------------------


async def test_reprocessing_needs_confirm_and_says_what_is_replaced() -> None:
    setup = Setup(writes=False)
    document = setup.pdf(1, "Scan")

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.reprocess([document])

    assert f"reprocess document {document} 'Scan'" in raised.value.message
    assert "replaced" in raised.value.message
    assert setup.untouched()


async def test_a_confirmed_reprocess_starts_a_task_per_document_and_produces_no_document() -> None:
    setup = Setup()
    first, second = setup.pdf(1), setup.pdf(1)

    result = await setup.service.reprocess([first, second], confirm=True, wait_seconds=5)

    assert (result.state, result.produced_ids, len(result.tasks)) == ("success", (), 2)
    assert result.message is None
    assert setup.state.reprocessed == [first, second]
    assert result.summary == "reprocess 2 documents (local OCR)"


async def test_reprocessing_can_use_the_remote_ocr_engine_and_takes_any_file_type() -> None:
    setup = Setup()
    text = setup.pdf(1, mime_type="text/plain")

    result = await setup.service.reprocess([text], remote_ocr=True, confirm=True)

    assert result.summary == "reprocess 1 document (remote OCR)"
    assert setup.state.reprocessed == [text]


# --- the exact words of every refusal and of the result ------------------------------------

PICK_DOCUMENTS = "Pass the document ids from search_documents."


async def refused(call: Awaitable[object]) -> tuple[str, str | None]:
    """The message and hint of the Validation `call` raises."""
    with pytest.raises(Validation) as raised:
        _ = await call
    return raised.value.message, raised.value.hint


async def test_the_refusals_of_a_selection_say_what_is_wrong_and_what_to_do() -> None:
    setup = Setup(writes=False)
    service = setup.service
    text = setup.pdf(1, "Notes", mime_type="text/plain")

    assert await refused(service.rotate([], 90)) == (
        "Name at least one document.",
        PICK_DOCUMENTS,
    )
    assert await refused(service.rotate(list(range(21)), 90)) == (
        "Work on at most 20 documents at once; got 21.",
        "Work in several steps.",
    )
    assert await refused(service.rotate([3, 3], 90)) == (
        "Document 3 is listed twice.",
        "List each document once.",
    )
    assert await refused(service.rotate([text], 90)) == (
        f"Document {text} is not a PDF (text/plain).",
        "Only PDF files can be changed this way.",
    )
    assert await refused(service.rotate([text], 45)) == (
        "45 degrees is not a quarter turn.",
        "Use 90, 180 or 270 (clockwise), or -90.",
    )


async def test_the_selection_bounds_are_inclusive() -> None:
    setup = Setup()
    twenty = [setup.pdf(1) for _ in range(MAX_DOCUMENTS)]

    result = await setup.service.rotate(twenty, 90)

    assert len(result.tasks) == MAX_DOCUMENTS


async def test_the_refusals_of_a_merge() -> None:
    setup = Setup(writes=False)
    first, second = setup.pdf(1), setup.pdf(1)
    text = setup.pdf(1, "Notes", mime_type="text/plain")

    assert await refused(setup.service.merge([first], None)) == (
        "Merging needs at least two documents.",
        "Name the documents to merge, in order.",
    )
    assert await refused(setup.service.merge([first, second], 99)) == (
        "Document 99 is not among the documents to merge.",
        "Take the metadata from one of the merged documents.",
    )
    assert (await refused(setup.service.merge([first, text], None)))[0].endswith("(text/plain).")


async def test_the_refusal_to_merge_without_confirm_has_the_exact_words() -> None:
    setup = Setup(writes=False)
    first, second, third = setup.pdf(1, "Alpha"), setup.pdf(1, "Beta"), setup.pdf(1, "Gamma")

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.merge([first, second, third], None, delete_originals=True)

    assert raised.value.message == (
        f"Nothing was changed. Confirming would merge document {first} 'Alpha', document {second} "
        f"'Beta' and document {third} 'Gamma' into one new document, in this order, and move the "
        "originals to the Paperless trash (they can be restored from there)."
    )


async def test_a_merge_of_three_names_them_all_in_the_summary() -> None:
    setup = Setup()
    first, second, third = setup.pdf(1), setup.pdf(1), setup.pdf(1)

    result = await setup.service.merge([first, second, third], None)

    assert result.summary == f"merge documents {first}, {second} and {third} into one new document"
    assert (result.operation, result.wait) == ("merge", "not_requested")


async def test_the_merge_of_a_text_file_is_refused() -> None:
    setup = Setup(writes=False)
    first, text = setup.pdf(1), setup.pdf(1, mime_type="text/plain")

    with pytest.raises(Validation, match="is not a PDF"):
        _ = await setup.service.merge([first, text], None)


async def test_the_refusals_of_a_split() -> None:
    setup = Setup(writes=False)
    document = setup.pdf(4)
    locked = setup.pdf(1, password="x")
    setup.document(locked).page_count = None
    service = setup.service

    assert await refused(service.split(document, ["1", "2"], output="new_version")) == (
        "A split makes several files, which cannot all be a version of the document.",
        "Use new_document or replace_original.",
    )
    assert await refused(service.split(document, ["1"])) == (
        "A split needs at least two parts and at most 20 parts.",
        "Give a page range per new document, e.g. '1-3'.",
    )
    assert await refused(service.split(document, ["1-2", "2"])) == (
        "Page 2 is in part 1 and part 2.",
        "Give each page to one part.",
    )
    assert await refused(service.split(locked, ["1", "2"])) == (
        f"The page count of document {locked} is unknown.",
        "Encrypted PDFs have none: remove the password first with remove_document_password.",
    )


async def test_a_split_may_have_as_many_parts_as_the_maximum_and_no_more() -> None:
    setup = Setup()
    document = setup.pdf(21)

    result = await setup.service.split(document, [str(n) for n in range(1, 21)])
    assert len(result.tasks) == 20
    with pytest.raises(Validation, match="at most 20 parts"):
        _ = await setup.service.split(document, [str(n) for n in range(1, 22)])


async def test_the_split_confirmation_and_summary_have_the_exact_words() -> None:
    setup = Setup(writes=False)
    document = setup.pdf(4, "Scan")

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.split(document, ["1-2", "3-4"], output="replace_original")

    assert raised.value.message == (
        f"Nothing was changed. Confirming would split document {document} 'Scan' into 2 new "
        "documents (pages 1-2 | pages 3-4) and move the original to the Paperless trash."
    )
    writing = Setup()
    result = await writing.service.split(writing.pdf(4), ["1-2", "3-4"])
    assert (result.operation, result.wait) == ("split", "not_requested")


async def test_the_refusals_of_deleting_pages() -> None:
    setup = Setup(writes=False)
    document = setup.pdf(3)

    assert await refused(setup.service.delete_pages(document, "1-3", confirm=True)) == (
        "Deleting every page leaves nothing.",
        "Delete the whole document with delete_document.",
    )


async def test_the_confirmations_of_deleting_pages_have_the_exact_words() -> None:
    setup = Setup(writes=False)
    document = setup.pdf(5, "Scan")
    tail = (
        "the document then shows a new version without them, and the earlier version with all "
        "pages stays until that version is deleted."
    )

    with pytest.raises(ConfirmationRequired) as version:
        _ = await setup.service.delete_pages(document, "2-3,5")
    with pytest.raises(ConfirmationRequired) as replacing:
        _ = await setup.service.delete_pages(document, "1", output="replace_original")

    assert version.value.message == (
        f"Nothing was changed. Confirming would delete pages 2-3 and 5 of document {document} "
        f"'Scan': {tail}"
    )
    assert replacing.value.message == (
        f"Nothing was changed. Confirming would delete page 1 of document {document} 'Scan' into "
        "a new document and move the original to the Paperless trash."
    )


async def test_deleting_pages_that_replace_the_original_say_so_in_the_result() -> None:
    setup = Setup()
    document = setup.pdf(3)

    result = await setup.service.delete_pages(
        document, "1", output="replace_original", confirm=True
    )

    assert result.summary == (
        f"delete page 1 of document {document} into a new document and move the original to the "
        "Paperless trash"
    )
    assert (result.operation, result.wait) == ("delete_pages", "not_requested")
    assert trashed(setup) == {document}


async def test_the_refusals_of_an_edit() -> None:
    setup = Setup(writes=False)
    document = setup.pdf(2)
    edit = setup.service.edit

    assert await refused(edit(document, [])) == (
        "Name at least one page and at most 500 pages.",
        "Each page of the result is one entry.",
    )
    assert await refused(edit(document, [PageSlot(page=1, rotate=45)])) == (
        "A rotation must be a multiple of 90 degrees, not 45.",
        "Use 0, 90, 180 or 270.",
    )
    assert await refused(edit(document, [PageSlot(page=1, document=-1)])) == (
        "Output files are numbered from 0.",
        "Use 0 for the first file, 1 for the second.",
    )
    assert await refused(edit(document, [PageSlot(page=1, document=1)])) == (
        "Output files must be numbered 0, 1, 2 and so on without gaps.",
        "Renumber the files so that none is skipped.",
    )
    assert await refused(
        edit(document, [PageSlot(page=1), PageSlot(page=2, document=1)], output="new_version")
    ) == (
        "A new version is a single file, but the pages go to several files.",
        "Use new_document or replace_original.",
    )


async def test_an_edit_of_exactly_the_maximum_number_of_pages_is_allowed() -> None:
    setup = Setup()
    document = setup.pdf(1)

    result = await setup.service.edit(document, [PageSlot(page=1)] * 500)

    assert result.operation == "edit_pdf"
    assert len(result.tasks) == 1


async def test_a_full_turn_is_not_a_turn_and_an_edit_summary_counts_pages_and_turns() -> None:
    setup = Setup()
    document = setup.pdf(2)
    slots = [PageSlot(page=1, rotate=360), PageSlot(page=2, rotate=-90), PageSlot(page=1)]

    result = await setup.service.edit(document, slots)

    assert result.summary == f"edit document {document} into 1 new document (3 pages, 1 turned)"
    assert result.wait == "not_requested"


async def test_a_single_page_edit_says_one_page() -> None:
    setup = Setup()
    document = setup.pdf(2)

    result = await setup.service.edit(document, [PageSlot(page=2)])

    assert result.summary == f"edit document {document} into 1 new document (1 page, 0 turned)"


async def test_the_confirmations_of_an_edit_have_the_exact_words() -> None:
    setup = Setup(writes=False)
    document = setup.pdf(4, "Scan")

    with pytest.raises(ConfirmationRequired) as version:
        _ = await setup.service.edit(document, [PageSlot(page=1)], output="new_version")
    with pytest.raises(ConfirmationRequired) as replacing:
        _ = await setup.service.edit(
            document, [PageSlot(page=1), PageSlot(page=2, document=1)], output="replace_original"
        )

    assert version.value.message == (
        f"Nothing was changed. Confirming would drop pages 2-4 of document {document} 'Scan': the "
        "document then shows a new version without them, and the earlier version with all pages "
        "stays until that version is deleted."
    )
    assert replacing.value.message == (
        f"Nothing was changed. Confirming would edit document {document} 'Scan' into 2 new "
        "documents (2 pages, 0 turned) and move the original to the Paperless trash."
    )


async def test_an_edit_that_replaces_the_original_says_so_in_the_result() -> None:
    setup = Setup()
    document = setup.pdf(2)

    result = await setup.service.edit(
        document, [PageSlot(page=2)], output="replace_original", confirm=True
    )

    assert result.summary == (
        f"edit document {document} into 1 new document (1 page, 0 turned) and move the original "
        "to the Paperless trash"
    )
    assert trashed(setup) == {document}


async def test_removing_a_password_has_exact_refusals_and_summaries() -> None:
    setup = Setup(writes=False)
    locked = setup.pdf(1, "Locked", password="s3cret")

    assert await refused(setup.service.remove_password(locked, "")) == (
        "The password is empty.",
        "Give the password that opens the file.",
    )
    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.remove_password(locked, "s3cret", output="replace_original")
    assert raised.value.message == (
        f"Nothing was changed. Confirming would remove the password of document {locked} "
        "'Locked' into a new document and move the original to the Paperless trash."
    )
    text = setup.pdf(1, mime_type="text/plain")
    with pytest.raises(Validation, match="is not a PDF"):
        _ = await setup.service.remove_password(text, "x")


async def test_a_confirmed_password_removal_that_replaces_the_original_trashes_it() -> None:
    setup = Setup()
    locked = setup.pdf(1, "Locked", password="s3cret")

    result = await setup.service.remove_password(
        locked, "s3cret", output="replace_original", confirm=True
    )

    assert result.summary == (
        f"remove the password of document {locked} into a new document and move the original to "
        "the Paperless trash"
    )
    assert (result.operation, result.wait) == ("remove_password", "not_requested")
    assert trashed(setup) == {locked}


async def test_the_refusal_to_reprocess_has_the_exact_words() -> None:
    setup = Setup(writes=False)
    first, second = setup.pdf(1, "Alpha"), setup.pdf(1, "Beta")

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.reprocess([first, second])

    assert raised.value.message == (
        f"Nothing was changed. Confirming would reprocess document {first} 'Alpha' and document "
        f"{second} 'Beta' (OCR and archiving run again; the archived PDF and the extracted text "
        "are replaced, so manual changes to the text are lost)."
    )


async def test_reprocessing_passes_the_ocr_choice_on_and_reports_its_operation() -> None:
    setup = Setup()
    asked: list[bool] = []

    class Spy(Slow):
        @override
        async def reprocess_documents(
            self, document_ids: Sequence[int], *, remote_ocr: bool
        ) -> tuple[str, ...]:
            asked.append(remote_ocr)
            return await super().reprocess_documents(document_ids, remote_ocr=remote_ocr)

    spy = Spy(setup.state, user=ADMIN[0], allow_writes=True, slow_reads=0)
    service = PdfService(spy, spy, TaskService(spy))
    document = setup.pdf(1)

    local = await service.reprocess([document], confirm=True)
    remote = await service.reprocess([document], remote_ocr=True, confirm=True)

    assert asked == [False, True]
    assert (local.operation, local.wait) == ("reprocess", "not_requested")
    assert remote.operation == "reprocess"


# --- the result ---------------------------------------------------------------------------


async def test_a_wait_of_one_second_is_a_wait() -> None:
    setup = Setup()

    result = await setup.service.rotate([setup.pdf(1)], 90, wait_seconds=1)

    assert result.wait == "finished"


async def test_the_waited_time_is_kept_to_a_tenth_of_a_second() -> None:
    setup = Setup(slow_reads=1)
    setup.clock.step = 0.123  # every read of the clock costs 0.123 s
    first, second = setup.pdf(1), setup.pdf(1)

    result = await setup.service.rotate([first, second], 90, wait_seconds=30)

    assert isinstance(result.waited_seconds, float)
    assert result.waited_seconds == round(result.waited_seconds, 1)
    assert result.waited_seconds > 0


async def test_a_failed_task_keeps_no_produced_documents_even_if_it_names_some() -> None:
    setup = Setup()
    setup.fake.failing = "boom"
    setup.fake.keep_documents = True

    result = await setup.service.rotate([setup.pdf(1)], 90, wait_seconds=1)

    assert result.produced_ids == ()
    assert result.tasks[0].document_ids == ()


async def test_a_task_that_names_no_document_has_no_message() -> None:
    setup = Setup()

    result = await setup.service.reprocess([setup.pdf(1)], confirm=True, wait_seconds=1)

    assert result.tasks[0].message is None
    assert result.message is None


async def test_the_message_of_a_result_without_tasks_is_the_exact_text() -> None:
    setup = Setup(permissions=EDITING)

    result = await setup.service.rotate([setup.pdf(1)], 90)

    assert result.message == (
        "Paperless accepted the request, but no task could be found for it. The Paperless user "
        "may not view tasks, or Paperless started none."
    )


class Recording(Slow):
    """The fake, remembering where the edits asked to put their results."""

    def __init__(self, state: FakeState) -> None:
        super().__init__(state, user=ADMIN[0], allow_writes=True, slow_reads=0)
        self.outputs: list[str] = []

    @override
    async def edit_pdf(
        self, document_id: int, pages: Sequence[PageSlot], *, output: Output
    ) -> tuple[str, ...]:
        self.outputs.append(output)
        return await super().edit_pdf(document_id, pages, output=output)


async def test_splitting_and_editing_make_new_documents_unless_told_otherwise() -> None:
    setup = Setup()
    spy = Recording(setup.state)
    service = PdfService(spy, spy, TaskService(spy))
    document = setup.pdf(3)

    _ = await service.split(document, ["1", "2"])
    _ = await service.edit(document, [PageSlot(page=1)])

    assert spy.outputs == ["new_document", "new_document"]


async def test_page_operations_refuse_a_file_that_is_not_a_pdf() -> None:
    setup = Setup(writes=False)
    text = setup.pdf(2, "Notes", mime_type="text/plain")
    service = setup.service

    with pytest.raises(Validation, match="is not a PDF"):
        _ = await service.split(text, ["1", "2"])
    with pytest.raises(Validation, match="is not a PDF"):
        _ = await service.delete_pages(text, "1", confirm=True)
    with pytest.raises(Validation, match="is not a PDF"):
        _ = await service.edit(text, [PageSlot(page=1)], confirm=True)


async def test_leaving_out_only_the_first_page_drops_it() -> None:
    setup = Setup(writes=False)
    document = setup.pdf(3, "Scan")

    with pytest.raises(ConfirmationRequired, match=rf"drop page 1 of document {document}"):
        _ = await setup.service.edit(
            document, [PageSlot(page=2), PageSlot(page=3)], output="new_version"
        )


async def test_the_waited_time_of_several_tasks_is_added_and_kept_to_a_tenth() -> None:
    setup = Setup(slow_reads=2)
    setup.clock.step = 0.123
    first, second = setup.pdf(1), setup.pdf(1)

    result = await setup.service.rotate([first, second], 90, wait_seconds=30)

    assert result.waited_seconds == 4.7
