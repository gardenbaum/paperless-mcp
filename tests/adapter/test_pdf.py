"""PaperlessPdfWriter on the Paperless 3.4.2 wire format (schema-guarded, PLMCP-23).

Every PDF endpoint answers `{"result": "OK"}` and names no task; the adapter finds the tasks in
the task list. The rows below are shaped like the ones recorded from 3.4.2.
"""

import json
from typing import TYPE_CHECKING

import httpx2
import pytest

from paperless_mcp.domain.errors import PermissionDenied, UpstreamUnavailable, Validation
from paperless_mcp.domain.pdf import PageSlot
from paperless_mcp.paperless.pdf import PaperlessPdfWriter

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from adapter.conftest import ClientFactory

pytestmark = pytest.mark.anyio

type Json = dict[str, object]

OK: Json = {"result": "OK"}


def row(number: int, task_type: str = "consume_file", filename: str | None = None) -> Json:
    """A task as `/api/tasks/` lists it on 3.4.2; the task id is derived from the row id."""
    return {
        "id": number,
        "task_id": f"00000000-0000-0000-0000-{number:012d}",
        "task_type": task_type,
        "task_type_display": task_type,
        "trigger_source": "api_upload" if task_type == "consume_file" else "manual",
        "trigger_source_display": "Manual",
        "status": "success",
        "status_display": "Success",
        "date_created": "2026-11-13T17:25:36.540908Z",
        "date_started": None,
        "date_done": None,
        "duration_seconds": None,
        "wait_time_seconds": None,
        "input_data": {} if filename is None else {"filename": filename, "mime_type": "x"},
        "result_data": None,
        "related_document_ids": [],
        "acknowledged": False,
        "owner": 2,
    }


def task_id(number: int) -> str:
    return f"00000000-0000-0000-0000-{number:012d}"


class Paperless:
    """A tiny Paperless: the task list the operation changes, and the requests it saw.

    The first `hidden_polls` reads of the list after the POST show `during` (the old list unless
    given), as if the tasks were not all queued yet. `refuse` answers the POST with a status and a
    body instead.
    """

    def __init__(
        self,
        before: list[Json],
        after: list[Json],
        *,
        hidden_polls: int = 0,
        during: list[Json] | None = None,
        refuse: tuple[int, str] | None = None,
    ) -> None:
        self.before: list[Json] = before
        self.after: list[Json] = after
        self.during: list[Json] = before if during is None else during
        self.hidden_polls: int = hidden_polls
        self.refuse: tuple[int, str] | None = refuse
        self.requests: list[tuple[str, str, dict[str, str], object]] = []
        self._posted: bool = False

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        payload: object = json.loads(request.content) if request.content else None
        self.requests.append((request.method, request.url.path, dict(request.url.params), payload))
        if request.method == "POST":
            self._posted = True
            if self.refuse is not None:
                status, text = self.refuse
                return httpx2.Response(status, text=text)
            return httpx2.Response(200, json=OK)
        rows = self.during if self._posted and self.hidden_polls > 0 else self.after
        if not self._posted:
            rows = self.before
        if self._posted and self.hidden_polls > 0:
            self.hidden_polls -= 1
        ordered = sorted(rows, key=lambda r: -int(str(r["id"])))
        size = int(request.url.params["page_size"])
        page = ordered[:size]
        return httpx2.Response(
            200, json={"count": len(rows), "next": None, "previous": None, "results": page}
        )

    @property
    def posts(self) -> list[tuple[str, str, dict[str, str], object]]:
        return [r for r in self.requests if r[0] == "POST"]


class Clock:
    """A clock the sleeps advance, so the bounded wait of the adapter needs no real time."""

    def __init__(self) -> None:
        self.now: float = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def writer(
    make_client: ClientFactory, server: Paperless, clock: Clock | None = None
) -> PaperlessPdfWriter:
    ticking = clock or Clock()
    return PaperlessPdfWriter(
        make_client(server),
        settle_seconds=1.0,
        poll_seconds=0.25,
        sleep=ticking.sleep,
        clock=ticking.time,
    )


OLD = [row(10, "consume_file", "plmcp.pdf"), row(11, "mail_fetch")]


async def test_a_rotation_posts_the_documents_and_returns_the_tasks_made_for_them(
    make_client: ClientFactory,
) -> None:
    server = Paperless(
        OLD,
        [
            *OLD,
            row(12, "consume_file", "7_rotated.pdf"),
            row(13, "consume_file", "4_rotated.pdf"),
            row(14, "consume_file", "99_rotated.pdf"),  # another document: not ours
            row(15, "consume_file", "7_unprotected.pdf"),  # another operation
            row(16, "mail_fetch"),
        ],
    )

    tasks = await writer(make_client, server).rotate_documents((4, 7), -90)

    assert tasks == (task_id(12), task_id(13))
    assert server.posts == [
        ("POST", "/api/documents/rotate/", {}, {"documents": [4, 7], "degrees": -90})
    ]


async def test_the_task_list_is_read_before_the_request_to_tell_new_tasks_from_old(
    make_client: ClientFactory,
) -> None:
    server = Paperless(OLD, [*OLD, row(12, "consume_file", "7_rotated.pdf")])

    _ = await writer(make_client, server).rotate_documents((7,), 90)

    methods = [(method, path) for method, path, _, _ in server.requests]
    assert methods[:2] == [("GET", "/api/tasks/"), ("POST", "/api/documents/rotate/")]
    assert server.requests[0][2] == {"ordering": "-id", "page_size": "1"}


async def test_old_tasks_of_the_same_kind_are_not_taken_for_new_ones(
    make_client: ClientFactory,
) -> None:
    before = [row(10, "consume_file", "7_rotated.pdf")]  # an earlier rotation of the same file
    server = Paperless(before, [*before, row(12, "consume_file", "7_rotated.pdf")])

    tasks = await writer(make_client, server).rotate_documents((7,), 90)

    assert tasks == (task_id(12),)


async def test_tasks_that_show_up_late_are_waited_for_until_all_are_there(
    make_client: ClientFactory,
) -> None:
    clock = Clock()
    server = Paperless(
        OLD,
        [*OLD, row(12, "consume_file", "7_rotated.pdf"), row(13, "consume_file", "4_rotated.pdf")],
        hidden_polls=2,
    )

    tasks = await writer(make_client, server, clock).rotate_documents((4, 7), 90)

    assert tasks == (task_id(12), task_id(13))
    assert clock.sleeps == [0.25, 0.25]  # two reads saw nothing yet; it stops at the third


async def test_a_task_that_never_shows_up_ends_the_wait_after_the_bound(
    make_client: ClientFactory,
) -> None:
    clock = Clock()
    server = Paperless(OLD, [*OLD, row(12, "consume_file", "7_rotated.pdf")])

    tasks = await writer(make_client, server, clock).rotate_documents((4, 7), 90)

    assert tasks == (task_id(12),)  # what was found; the other task never appeared
    assert clock.sleeps == [0.25, 0.25, 0.25, 0.25]  # a bound of one second, in quarters


async def test_a_user_who_may_not_view_tasks_still_gets_the_operation_done(
    make_client: ClientFactory,
) -> None:
    def deny_tasks(request: httpx2.Request) -> httpx2.Response:
        if request.method == "GET":
            return httpx2.Response(403, json={"detail": "You do not have permission."})
        return httpx2.Response(200, json=OK)

    clock = Clock()
    server = deny_tasks
    tasks = await PaperlessPdfWriter(
        make_client(server),
        settle_seconds=1.0,
        poll_seconds=0.25,
        sleep=clock.sleep,
        clock=clock.time,
    ).rotate_documents((7,), 90)

    assert tasks == ()
    assert clock.sleeps == []  # nothing to wait for


async def test_merging_names_the_metadata_document_and_the_deletion_of_the_originals(
    make_client: ClientFactory,
) -> None:
    server = Paperless(OLD, [*OLD, row(12, "consume_file", "5_9_merged.pdf")])

    tasks = await writer(make_client, server).merge_documents(
        (5, 9), metadata_document_id=9, delete_originals=True
    )

    assert tasks == (task_id(12),)
    assert server.posts == [
        (
            "POST",
            "/api/documents/merge/",
            {},
            {"documents": [5, 9], "metadata_document_id": 9, "delete_originals": True},
        )
    ]


async def test_merging_without_a_metadata_document_sends_none(make_client: ClientFactory) -> None:
    server = Paperless(OLD, [*OLD, row(12, "consume_file", "9_5_merged.pdf")])

    tasks = await writer(make_client, server).merge_documents(
        (9, 5), metadata_document_id=None, delete_originals=False
    )

    assert tasks == (task_id(12),)  # the merged file is named after the documents, in any order
    assert server.posts[0][3] == {
        "documents": [9, 5],
        "metadata_document_id": None,
        "delete_originals": False,
    }


async def test_a_delete_of_originals_adds_a_task_that_is_not_reported(
    make_client: ClientFactory,
) -> None:
    server = Paperless(
        OLD,
        [*OLD, row(12, "consume_file", "5_9_merged.pdf"), row(13, "bulk_delete")],
    )

    tasks = await writer(make_client, server).merge_documents(
        (5, 9), metadata_document_id=None, delete_originals=True
    )

    assert tasks == (task_id(12),)


PAGES = (PageSlot(page=2), PageSlot(page=1, rotate=90), PageSlot(page=3, document=1))


async def test_an_edit_into_new_documents_sends_the_pages_and_finds_one_task_per_file(
    make_client: ClientFactory,
) -> None:
    server = Paperless(
        OLD,
        [
            *OLD,
            row(12, "consume_file", "8_edit_1.pdf"),
            row(13, "consume_file", "8_edit_2.pdf"),
        ],
    )

    tasks = await writer(make_client, server).edit_pdf(8, PAGES, output="new_document")

    assert tasks == (task_id(12), task_id(13))
    assert server.posts == [
        (
            "POST",
            "/api/documents/edit_pdf/",
            {},
            {
                "documents": [8],
                "operations": [
                    {"page": 2, "rotate": 0, "doc": 0},
                    {"page": 1, "rotate": 90, "doc": 0},
                    {"page": 3, "rotate": 0, "doc": 1},
                ],
                "update_document": False,
                "delete_original": False,
            },
        )
    ]


async def test_an_edit_as_a_new_version_finds_the_edited_file(make_client: ClientFactory) -> None:
    server = Paperless(OLD, [*OLD, row(12, "consume_file", "8_edited.pdf")])

    tasks = await writer(make_client, server).edit_pdf(8, (PageSlot(page=1),), output="new_version")

    assert tasks == (task_id(12),)
    body = server.posts[0][3]
    assert isinstance(body, dict)
    assert (body["update_document"], body["delete_original"]) == (True, False)


async def test_an_edit_that_replaces_the_original_asks_for_its_deletion(
    make_client: ClientFactory,
) -> None:
    server = Paperless(OLD, [*OLD, row(12, "consume_file", "8_edit_1.pdf"), row(13, "bulk_delete")])

    tasks = await writer(make_client, server).edit_pdf(
        8, (PageSlot(page=1),), output="replace_original"
    )

    assert tasks == (task_id(12),)
    body = server.posts[0][3]
    assert isinstance(body, dict)
    assert (body["update_document"], body["delete_original"]) == (False, True)


@pytest.mark.parametrize(
    ("output", "flags", "filename"),
    [
        ("new_document", (False, False), "8_unprotected.pdf"),
        ("new_version", (True, False), "8_unprotected.pdf"),
        ("replace_original", (False, True), "8_unprotected.pdf"),
    ],
)
async def test_removing_a_password_sends_it_with_where_the_copy_goes(
    make_client: ClientFactory, output: str, flags: tuple[bool, bool], filename: str
) -> None:
    server = Paperless(OLD, [*OLD, row(12, "consume_file", filename)])

    tasks = await writer(make_client, server).remove_password(8, "s3cret", output=output)  # pyright: ignore[reportArgumentType] -- the parameter is the literal under test

    assert tasks == (task_id(12),)
    assert server.posts == [
        (
            "POST",
            "/api/documents/remove_password/",
            {},
            {
                "documents": [8],
                "password": "s3cret",
                "update_document": flags[0],
                "delete_original": flags[1],
            },
        )
    ]


async def test_a_file_without_a_password_starts_no_task_and_ends_the_wait_after_the_bound(
    make_client: ClientFactory,
) -> None:
    clock = Clock()
    server = Paperless(OLD, OLD)

    tasks = await writer(make_client, server, clock).remove_password(8, "x", output="new_document")

    assert tasks == ()
    assert sum(clock.sleeps) == 1.0


async def test_reprocessing_finds_one_task_per_document(make_client: ClientFactory) -> None:
    server = Paperless(
        OLD,
        [*OLD, row(12, "reprocess_document"), row(13, "reprocess_document"), row(14, "mail_fetch")],
    )

    tasks = await writer(make_client, server).reprocess_documents((4, 7), remote_ocr=True)

    assert tasks == (task_id(12), task_id(13))
    assert server.posts == [
        (
            "POST",
            "/api/documents/reprocess/",
            {},
            {"documents": [4, 7], "remote_ocr": True},
        )
    ]


@pytest.mark.parametrize(
    ("status", "text", "error", "message"),
    [
        (403, "Insufficient permissions", PermissionDenied, "may not change"),
        (400, '{"documents": ["Some documents in documents don\'t exist."]}', Validation, "exist"),
    ],
)
async def test_paperless_refusals_become_domain_errors_and_nothing_is_polled(
    make_client: ClientFactory, status: int, text: str, error: type[Exception], message: str
) -> None:
    server = Paperless(OLD, OLD, refuse=(status, text))

    with pytest.raises(error, match=message):
        _ = await writer(make_client, server).rotate_documents((4,), 90)

    assert [method for method, _, _, _ in server.requests] == ["GET", "POST"]


async def test_the_request_is_sent_once_even_when_paperless_is_busy(
    make_client: ClientFactory,
) -> None:
    server = Paperless(OLD, OLD, refuse=(503, "busy"))

    with pytest.raises(UpstreamUnavailable):
        _ = await writer(make_client, server).rotate_documents((4,), 90)

    assert len(server.posts) == 1


async def test_a_wrong_password_is_told_apart_from_other_refusals(
    make_client: ClientFactory,
) -> None:
    text = "Error performing password removal, check logs for more detail."
    server = Paperless(OLD, OLD, refuse=(400, text))

    with pytest.raises(Validation, match="password"):
        _ = await writer(make_client, server).remove_password(8, "nope", output="new_document")


async def test_an_edit_paperless_cannot_perform_says_the_file_may_not_be_a_pdf(
    make_client: ClientFactory,
) -> None:
    text = "Error performing PDF edit, check logs for more detail."
    server = Paperless(OLD, OLD, refuse=(400, text))

    with pytest.raises(Validation, match="PDF"):
        _ = await writer(make_client, server).edit_pdf(
            8, (PageSlot(page=1),), output="new_document"
        )


type Call = Callable[[PaperlessPdfWriter], Awaitable[tuple[str, ...]]]

EDIT_PAGES = (PageSlot(page=1), PageSlot(page=2, document=1))
EVERY_OPERATION: list[tuple[str, Call, list[Json]]] = [
    (
        "rotate",
        lambda w: w.rotate_documents((7,), 90),
        [row(12, "consume_file", "7_rotated.pdf")],
    ),
    (
        "merge",
        lambda w: w.merge_documents((7, 9), metadata_document_id=None, delete_originals=False),
        [row(12, "consume_file", "7_9_merged.pdf")],
    ),
    (
        "edit-new",
        lambda w: w.edit_pdf(7, EDIT_PAGES, output="new_document"),
        [row(12, "consume_file", "7_edit_1.pdf"), row(13, "consume_file", "7_edit_2.pdf")],
    ),
    (
        "edit-version",
        lambda w: w.edit_pdf(7, (PageSlot(page=1),), output="new_version"),
        [row(12, "consume_file", "7_edited.pdf")],
    ),
    (
        "password",
        lambda w: w.remove_password(7, "x", output="new_document"),
        [row(12, "consume_file", "7_unprotected.pdf")],
    ),
    (
        "reprocess",
        lambda w: w.reprocess_documents((7,), remote_ocr=False),
        [row(12, "reprocess_document")],
    ),
]


@pytest.mark.parametrize(
    ("call", "made"),
    [pytest.param(call, made, id=name) for name, call, made in EVERY_OPERATION],
)
async def test_every_operation_stops_looking_as_soon_as_all_its_tasks_are_there(
    make_client: ClientFactory, call: Call, made: list[Json]
) -> None:
    """A task count that is too high would wait out the bound for tasks that never come."""
    clock = Clock()
    server = Paperless(OLD, [*OLD, *made])

    tasks = await call(writer(make_client, server, clock))

    assert tasks == tuple(task_id(int(str(r["id"]))) for r in made)
    assert clock.sleeps == []


async def test_only_the_tasks_of_the_documents_and_files_asked_for_are_taken(
    make_client: ClientFactory,
) -> None:
    other = [
        row(12, "mail_fetch", "8_edit_1.pdf"),  # right name, wrong kind
        row(13, "consume_file", "9_edit_1.pdf"),  # another document
        row(14, "consume_file", "8_edit_x.pdf"),  # not an output file
        row(15, "consume_file", "8_edited.pdf"),  # a version: this edit makes documents
        row(16, "consume_file", "x8_edit_1.pdf"),
        row(17, "consume_file", "8_edit_1.pdf.bak"),
        row(18, "consume_file"),  # no filename at all
        row(19, "consume_file", "8_edit_1.pdf"),  # the one
    ]
    server = Paperless(OLD, [*OLD, *other])
    clock = Clock()

    tasks = await writer(make_client, server, clock).edit_pdf(
        8, (PageSlot(page=1),), output="new_document"
    )

    assert tasks == (task_id(19),)


async def test_only_the_merged_file_of_exactly_these_documents_is_taken(
    make_client: ClientFactory,
) -> None:
    other = [
        row(12, "mail_fetch", "5_9_merged.pdf"),  # wrong kind
        row(13, "consume_file", "5_merged.pdf"),  # fewer documents
        row(14, "consume_file", "5_9_7_merged.pdf"),  # more documents
        row(15, "consume_file", "5_9.pdf"),  # not a merge
        row(16, "consume_file", "merged.pdf"),
        row(17, "consume_file", "5_9_merged.pdf"),  # the one
    ]
    server = Paperless(OLD, [*OLD, *other])

    tasks = await writer(make_client, server).merge_documents(
        (9, 5), metadata_document_id=None, delete_originals=False
    )

    assert tasks == (task_id(17),)


async def test_the_unprotected_file_of_this_document_only_is_taken(
    make_client: ClientFactory,
) -> None:
    other = [
        row(12, "mail_fetch", "8_unprotected.pdf"),
        row(13, "consume_file", "9_unprotected.pdf"),
        row(14, "consume_file", "8_unprotected.pdf"),
    ]
    server = Paperless(OLD, [*OLD, *other])

    tasks = await writer(make_client, server).remove_password(8, "x", output="new_version")

    assert tasks == (task_id(14),)


async def test_an_empty_task_list_makes_every_new_task_new(make_client: ClientFactory) -> None:
    server = Paperless([], [row(1, "consume_file", "7_rotated.pdf")])

    tasks = await writer(make_client, server).rotate_documents((7,), 90)

    assert tasks == (task_id(1),)


async def test_the_lookup_asks_for_the_newest_hundred_tasks(make_client: ClientFactory) -> None:
    server = Paperless(OLD, [*OLD, row(12, "consume_file", "7_rotated.pdf")])

    _ = await writer(make_client, server).rotate_documents((7,), 90)

    gets = [params for method, _, params, _ in server.requests if method == "GET"]
    assert gets == [
        {"ordering": "-id", "page_size": "1"},
        {"ordering": "-id", "page_size": "100"},
    ]


async def test_an_edit_with_no_pages_is_sent_and_refused_by_paperless(
    make_client: ClientFactory,
) -> None:
    text = '{"operations": ["This list may not be empty."]}'
    server = Paperless(OLD, OLD, refuse=(400, text))

    with pytest.raises(Validation, match="operations: This list may not be empty"):
        _ = await writer(make_client, server).edit_pdf(8, (), output="new_document")


async def test_a_400_with_details_is_not_taken_for_a_plain_failure(
    make_client: ClientFactory,
) -> None:
    text = '{"non_field_errors": ["Page 9 is out of bounds for document with 3 pages."]}'
    server = Paperless(OLD, OLD, refuse=(400, text))

    with pytest.raises(Validation, match="Page 9 is out of bounds"):
        _ = await writer(make_client, server).edit_pdf(
            8, (PageSlot(page=9),), output="new_document"
        )
    with pytest.raises(Validation, match="Page 9 is out of bounds"):
        _ = await writer(make_client, server).remove_password(8, "x", output="new_document")


async def test_the_refusals_have_the_exact_words(make_client: ClientFactory) -> None:
    denied = Paperless(OLD, OLD, refuse=(403, "Insufficient permissions"))
    plain = Paperless(OLD, OLD, refuse=(400, "Error performing PDF edit, check logs."))

    with pytest.raises(PermissionDenied) as permission:
        _ = await writer(make_client, denied).rotate_documents((4,), 90)
    with pytest.raises(Validation) as edit:
        _ = await writer(make_client, plain).edit_pdf(8, (PageSlot(page=1),), output="new_document")
    with pytest.raises(Validation) as password:
        _ = await writer(make_client, plain).remove_password(8, "x", output="new_document")

    hint = (
        "Changing a PDF needs the permission to change documents and a change grant on (or "
        "ownership of) every document; a new document needs the permission to add documents, "
        "and deleting an original also needs ownership and the permission to delete."
    )
    assert (permission.value.message, permission.value.hint) == (
        "The Paperless user may not change these documents.",
        hint,
    )
    assert (edit.value.message, edit.value.hint) == (
        "Paperless could not edit the file: it may not be a PDF.",
        "Only PDF files can be edited; check the document's type.",
    )
    assert (password.value.message, password.value.hint) == (
        "Paperless could not remove the password: it is wrong, or the file is not a PDF.",
        "Check the password; it is case sensitive.",
    )


async def test_the_files_of_an_edit_that_show_up_one_by_one_are_all_waited_for(
    make_client: ClientFactory,
) -> None:
    clock = Clock()
    server = Paperless(
        OLD,
        [*OLD, row(12, "consume_file", "8_edit_1.pdf"), row(13, "consume_file", "8_edit_2.pdf")],
        hidden_polls=1,
        during=[*OLD, row(12, "consume_file", "8_edit_1.pdf")],
    )

    tasks = await writer(make_client, server, clock).edit_pdf(
        8, (PageSlot(page=1), PageSlot(page=2, document=1)), output="new_document"
    )

    assert tasks == (task_id(12), task_id(13))
    assert clock.sleeps == [0.25]
