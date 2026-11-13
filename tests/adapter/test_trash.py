"""PaperlessTrashReader and PaperlessTrashWriter on responses recorded from Paperless 3.4.2."""

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx2
import pytest

from paperless_mcp.domain.errors import NotFound, PermissionDenied, Validation
from paperless_mcp.paperless.trash import PaperlessTrashReader, PaperlessTrashWriter

if TYPE_CHECKING:
    from adapter.conftest import ClientFactory

pytestmark = pytest.mark.anyio

type Json = dict[str, object]

# A deleted document, as GET /api/trash/ lists it (the full document, plus deleted_at).
DOCUMENT: Json = {
    "id": 3301,
    "correspondent": None,
    "document_type": None,
    "storage_path": None,
    "title": "plmcp27-probe-b",
    "content": "plmcp27-probe-b text",
    "tags": [3],
    "created": "2026-11-13",
    "created_date": "2026-11-13",
    "modified": "2026-11-13T14:26:54.634974Z",
    "added": "2026-11-13T14:26:54.576847Z",
    "deleted_at": "2026-11-13T14:26:55.220835Z",
    "archive_serial_number": None,
    "original_file_name": "plmcp27-probe-b.txt",
    "archived_file_name": None,
    "duplicate_documents": [],
    "owner": 2,
    "user_can_change": True,
    "is_shared_by_requester": False,
    "notes": [],
    "custom_fields": [],
    "page_count": None,
    "mime_type": "text/plain",
    "root_document": None,
    "versions": [
        {
            "id": 3301,
            "added": "2026-11-13T14:26:54.576847Z",
            "version_label": None,
            "checksum": "b5989a99fade41ed36af961e918ef43da955b8b08a4c8aa78430f2e6b7da43cb",
            "is_root": True,
        }
    ],
}
# A version deleted from document 3304: its entry carries the root's title and id.
VERSION: Json = {
    **DOCUMENT,
    "id": 3305,
    "title": "plmcp27-ver-root",
    "deleted_at": "2026-11-13T14:28:23.575758Z",
    "root_document": 3304,
    "versions": [
        {
            "id": 3304,
            "added": "2026-11-13T14:26:54.576847Z",
            "version_label": None,
            "checksum": "b5989a99fade41ed36af961e918ef43da955b8b08a4c8aa78430f2e6b7da43cb",
            "is_root": True,
        }
    ],
}


class Recorded:
    """Answers every request with one body and records method, path, query and JSON body."""

    def __init__(self, body: object, status: int = 200, *, html: bool = False) -> None:
        self.body: object = body
        self.status: int = status
        self.html: bool = html
        self.sent: list[tuple[str, str, dict[str, str], object]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        payload: object = json.loads(request.content) if request.content else None
        self.sent.append((request.method, request.url.path, dict(request.url.params), payload))
        if self.html:
            return httpx2.Response(
                self.status, text=str(self.body), headers={"Content-Type": "text/html"}
            )
        return httpx2.Response(self.status, json=self.body)


def listing(*rows: Json, count: int | None = None, after: str | None = None) -> Json:
    link = f"http://paperless.test/api/trash/?{after}" if after else None
    return {"count": len(rows) if count is None else count, "next": link, "previous": None,
            "results": list(rows)}  # fmt: skip


async def test_the_trash_is_listed_a_page_at_a_time(make_client: ClientFactory) -> None:
    server = Recorded(listing(DOCUMENT, VERSION, count=944, after="page=3&page_size=2"))

    page = await PaperlessTrashReader(make_client(server)).list_trash(page=2, page_size=2)

    assert server.sent == [("GET", "/api/trash/", {"page": "2", "page_size": "2"}, None)]
    assert (page.count, page.next_page) == (944, 3)
    first, second = page.entries
    assert (first.id, first.title, first.version_of) == (3301, "plmcp27-probe-b", None)
    assert first.deleted_at == datetime(2026, 11, 13, 14, 26, 55, 220835, tzinfo=UTC)
    assert (second.id, second.title, second.version_of) == (3305, "plmcp27-ver-root", 3304)


async def test_the_last_page_has_no_next_page(make_client: ClientFactory) -> None:
    server = Recorded(listing())

    page = await PaperlessTrashReader(make_client(server)).list_trash(page=1, page_size=25)

    assert (page.count, page.next_page, page.entries) == (0, None, ())


async def test_a_page_beyond_the_last_is_not_found_with_a_hint(make_client: ClientFactory) -> None:
    server = Recorded({"detail": "Invalid page."}, 404)

    with pytest.raises(NotFound) as raised:
        _ = await PaperlessTrashReader(make_client(server)).list_trash(page=9, page_size=25)

    assert raised.value.message == "Page 9 of the trash does not exist."
    assert (
        raised.value.hint == "List the trash with page=1; count and next_page tell how far it goes."
    )


async def test_listing_without_the_view_permission_is_denied(make_client: ClientFactory) -> None:
    server = Recorded({"detail": "You do not have permission to perform this action."}, 403)

    with pytest.raises(PermissionDenied):
        _ = await PaperlessTrashReader(make_client(server)).list_trash(page=1, page_size=25)


@pytest.mark.parametrize(
    ("method", "action"), [("restore_from_trash", "restore"), ("empty_trash", "empty")]
)
async def test_the_ids_are_posted_with_the_action(
    make_client: ClientFactory, method: str, action: str
) -> None:
    server = Recorded({"result": "OK", "doc_ids": [3300, 3301]})

    await getattr(PaperlessTrashWriter(make_client(server)), method)((3300, 3301))

    assert server.sent == [
        ("POST", "/api/trash/", {}, {"action": action, "documents": [3300, 3301]})
    ]


@pytest.mark.parametrize(
    ("method", "action"), [("restore_from_trash", "restore"), ("empty_trash", "empty")]
)
async def test_a_request_without_ids_is_refused_before_it_is_sent(
    make_client: ClientFactory, method: str, action: str
) -> None:
    # Paperless reads a missing list as "everything" (empty) or fails (restore): never send it.
    server = Recorded({"result": "OK", "doc_ids": []})

    with pytest.raises(Validation) as raised:
        await getattr(PaperlessTrashWriter(make_client(server)), method)(())

    assert raised.value.message == f"Name at least one trash entry to {action}."
    assert raised.value.hint == "Pass the ids from list_trash."
    assert server.sent == []


@pytest.mark.parametrize("method", ["restore_from_trash", "empty_trash"])
async def test_ids_that_are_not_in_the_trash_are_a_validation_error_with_a_hint(
    make_client: ClientFactory, method: str
) -> None:
    body = {"documents": ["Some documents in the list have not yet been deleted."]}
    server = Recorded(body, 400)

    with pytest.raises(Validation) as raised:
        await getattr(PaperlessTrashWriter(make_client(server)), method)((7,))

    assert raised.value.message == (
        "Paperless rejected the request: documents: Some documents in the list have not yet been "
        "deleted."
    )
    assert raised.value.hint == "Check the ids with list_trash; each must be in the trash."


async def test_restoring_a_version_of_a_trashed_document_names_the_document_to_restore(
    make_client: ClientFactory,
) -> None:
    body = {"documents": ["Restore the root document instead of one of its versions."]}
    server = Recorded(body, 400)

    with pytest.raises(Validation, match="Restore the root document instead"):
        await PaperlessTrashWriter(make_client(server)).restore_from_trash((3305,))


@pytest.mark.parametrize("method", ["restore_from_trash", "empty_trash"])
async def test_a_user_without_the_delete_permission_is_denied(
    make_client: ClientFactory, method: str
) -> None:
    server = Recorded({"detail": "You do not have permission to perform this action."}, 403)

    with pytest.raises(PermissionDenied) as raised:
        await getattr(PaperlessTrashWriter(make_client(server)), method)((7,))

    assert raised.value.message == "The Paperless user may not restore or empty these entries."
    assert raised.value.hint == (
        "It needs the permission to delete documents and must own every document (or it must "
        "have no owner)."
    )


async def test_a_document_of_someone_else_is_denied_as_an_html_403(
    make_client: ClientFactory,
) -> None:
    # TrashView answers HttpResponseForbidden('Insufficient permissions'), text/html.
    server = Recorded("Insufficient permissions", 403, html=True)

    with pytest.raises(PermissionDenied, match="may not restore or empty"):
        await PaperlessTrashWriter(make_client(server)).empty_trash((7,))


async def test_the_writer_is_not_retried(make_client: ClientFactory) -> None:
    server = Recorded("Server Error", 503, html=True)

    with pytest.raises(Exception, match="temporarily unavailable"):
        await PaperlessTrashWriter(make_client(server)).empty_trash((7,))

    assert len(server.sent) == 1
