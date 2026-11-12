"""Notes and deletion of PaperlessDocumentWriter on the Paperless 3.4.2 wire format."""

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx2
import pytest

from adapter.conftest import streaming
from paperless_mcp.domain.errors import (
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    UpstreamUnavailable,
    Validation,
)
from paperless_mcp.paperless.documents import PaperlessDocumentWriter

if TYPE_CHECKING:
    from adapter.conftest import ClientFactory

pytestmark = pytest.mark.anyio

type Json = dict[str, object]

ADMIN: Json = {"id": 2, "username": "admin", "first_name": "", "last_name": ""}
FIRST: Json = {
    "id": 2,
    "note": "first note",
    "created": "2026-11-12T22:13:06.976346Z",
    "user": ADMIN,
}
SECOND: Json = {
    "id": 3,
    "note": "second note",
    "created": "2026-11-12T22:13:07.094018Z",
    "user": ADMIN,
}


class Recorded:
    """Answers every request with one body and records method, path, query and JSON body."""

    def __init__(self, body: object, status: int = 200) -> None:
        self.body: object = body
        self.status: int = status
        self.sent: list[tuple[str, str, dict[str, str], object]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        payload: object = json.loads(request.content) if request.content else None
        self.sent.append((request.method, request.url.path, dict(request.url.params), payload))
        if self.body is None:
            return httpx2.Response(self.status)
        return httpx2.Response(self.status, json=self.body)


async def test_a_note_is_posted_as_text_and_the_new_one_is_picked_from_the_answer(
    make_client: ClientFactory,
) -> None:
    # Paperless answers with every note of the document, oldest first; the new one has the
    # highest id.
    server = Recorded([FIRST, SECOND])

    note = await PaperlessDocumentWriter(make_client(server)).add_note(1, "second note")

    assert server.sent == [("POST", "/api/documents/1/notes/", {}, {"note": "second note"})]
    assert (note.id, note.note, note.user) == (3, "second note", "admin")
    assert note.created == datetime(2026, 11, 12, 22, 13, 7, 94018, tzinfo=UTC)


async def test_the_new_note_is_the_highest_id_whatever_the_order() -> None:
    client, _ = streaming(lambda _: httpx2.Response(200, json=[SECOND, FIRST]))

    note = await PaperlessDocumentWriter(client).add_note(1, "second note")

    assert note.id == 3


async def test_a_save_error_in_the_answer_is_a_validation_error() -> None:
    # Paperless answers 200 with an error object, not a list, when it cannot save the note.
    saved_nothing = {"error": "Error saving note, check logs for more detail."}
    client, _ = streaming(lambda _: httpx2.Response(200, json=saved_nothing))

    with pytest.raises(Validation) as raised:
        _ = await PaperlessDocumentWriter(client).add_note(1, "x")

    assert raised.value.message == "Paperless could not save the note."
    assert raised.value.hint == "Check the Paperless log for the reason."


async def test_an_answer_without_notes_is_unexpected() -> None:
    client, _ = streaming(lambda _: httpx2.Response(200, json=[]))

    with pytest.raises(PaperlessMcpError, match="unexpected response"):
        _ = await PaperlessDocumentWriter(client).add_note(1, "x")


@pytest.mark.parametrize(
    ("status", "error"),
    [(404, NotFound), (403, PermissionDenied), (400, Validation)],
)
async def test_a_refused_note_is_a_domain_error(
    make_client: ClientFactory, status: int, error: type[Exception]
) -> None:
    server = Recorded({"detail": "Not found."}, status)

    with pytest.raises(error):
        _ = await PaperlessDocumentWriter(make_client(server)).add_note(9, "x")


async def test_a_missing_document_for_a_note_names_the_search_tool(
    make_client: ClientFactory,
) -> None:
    server = Recorded({"detail": "Not found."}, 404)

    with pytest.raises(NotFound, match="No document 9") as raised:
        _ = await PaperlessDocumentWriter(make_client(server)).add_note(9, "x")

    assert raised.value.hint
    assert "search_documents" in raised.value.hint


async def test_a_note_is_deleted_by_id_in_the_query(make_client: ClientFactory) -> None:
    server = Recorded([SECOND])

    await PaperlessDocumentWriter(make_client(server)).delete_note(1, 2)

    assert server.sent == [("DELETE", "/api/documents/1/notes/", {"id": "2"}, None)]


async def test_a_note_that_is_not_there_is_not_found_with_a_hint(
    make_client: ClientFactory,
) -> None:
    server = Recorded({"detail": "No Note matches the given query."}, 404)

    with pytest.raises(NotFound) as raised:
        await PaperlessDocumentWriter(make_client(server)).delete_note(1, 7)

    assert raised.value.message == "No note 7 exists on document 1 that this user may see."
    assert raised.value.hint == "List the notes with get_document and include notes."


async def test_a_note_the_user_may_not_delete_is_permission_denied(
    make_client: ClientFactory,
) -> None:
    server = Recorded({"detail": "You do not have permission to perform this action."}, 403)

    with pytest.raises(PermissionDenied):
        await PaperlessDocumentWriter(make_client(server)).delete_note(1, 2)


async def test_a_document_is_deleted_with_a_bare_delete(make_client: ClientFactory) -> None:
    server = Recorded(None, 204)

    await PaperlessDocumentWriter(make_client(server)).delete_document(1)

    assert server.sent == [("DELETE", "/api/documents/1/", {}, None)]


@pytest.mark.parametrize(
    ("status", "error"),
    [(404, NotFound), (403, PermissionDenied)],
)
async def test_a_document_that_cannot_be_deleted_is_a_domain_error(
    make_client: ClientFactory, status: int, error: type[Exception]
) -> None:
    server = Recorded({"detail": "No Document matches the given query."}, status)

    with pytest.raises(error):
        await PaperlessDocumentWriter(make_client(server)).delete_document(9)


async def test_a_missing_document_is_not_found_with_a_hint(make_client: ClientFactory) -> None:
    server = Recorded({"detail": "No Document matches the given query."}, 404)

    with pytest.raises(NotFound, match="No document 9") as raised:
        await PaperlessDocumentWriter(make_client(server)).delete_document(9)

    assert raised.value.hint
    assert "search_documents" in raised.value.hint


class Failing:
    """Answers every request with 503 and counts them."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.calls.append(request.url.path)
        return httpx2.Response(503)


async def test_a_failed_note_deletion_is_never_retried() -> None:
    # A repeated DELETE would answer 404 after the first one succeeded: report the failure once.
    server = Failing()
    client, sleep = streaming(server)

    with pytest.raises(UpstreamUnavailable):
        await PaperlessDocumentWriter(client).delete_note(1, 2)

    assert len(server.calls) == 1
    assert sleep.delays == []


async def test_a_failed_document_deletion_is_never_retried() -> None:
    server = Failing()
    client, sleep = streaming(server)

    with pytest.raises(UpstreamUnavailable):
        await PaperlessDocumentWriter(client).delete_document(1)

    assert len(server.calls) == 1
    assert sleep.delays == []


async def test_a_failed_note_post_is_not_retried_either() -> None:
    calls: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.url.path)
        return httpx2.Response(503)

    client, _ = streaming(handler)

    with pytest.raises(UpstreamUnavailable):
        _ = await PaperlessDocumentWriter(client).add_note(1, "x")

    assert len(calls) == 1
