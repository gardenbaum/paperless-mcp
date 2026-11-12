"""PaperlessDocumentReader on responses recorded from the Paperless 3.4.2 E2E stack."""

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Literal, cast

import httpx2
import pytest

from adapter.test_client import streaming
from paperless_mcp.domain.documents import (
    CustomFieldValue,
    DocumentFile,
    DocumentHit,
    DocumentQuery,
)
from paperless_mcp.domain.errors import (
    FeatureUnavailable,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    Validation,
)
from paperless_mcp.paperless.documents import (
    FIELDS,
    THUMBNAIL_LIMIT,
    PaperlessDocumentReader,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from adapter.conftest import ClientFactory

pytestmark = pytest.mark.anyio

type Json = dict[str, object]

ROW: Json = {
    "id": 1,
    "title": "e2e-public-invoice",
    "created": "2026-11-11",
    "added": "2026-11-11T19:47:09.049803Z",
    "correspondent": 1,
    "document_type": 1,
    "storage_path": 1,
    "tags": [1, 3, 2],
    "custom_fields": [
        {"value": True, "field": 4},
        {"value": [2], "field": 8},
        {"value": "1sg5d8f1333w5Qak", "field": 10},
    ],
}
HIT: Json = {
    "score": 3.84,
    "highlights": "E2E public <b>invoice</b> ACME 2026",
    "note_highlights": None,
    "rank": 1,
}


class Documents:
    """Answers GET /api/documents/ with `rows` and records the query parameters sent."""

    def __init__(self, *rows: Json, next_link: str | None = None) -> None:
        self.rows: list[Json] = list(rows)
        self.next_link: str | None = next_link
        self.sent: list[dict[str, str]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.sent.append(dict(request.url.params))
        body = {
            "count": len(self.rows),
            "next": self.next_link,
            "previous": None,
            "results": self.rows,
        }
        return httpx2.Response(200, json=body)


async def test_a_search_asks_for_one_page_of_named_fields_and_maps_the_rows(
    make_client: ClientFactory,
) -> None:
    server = Documents(ROW, next_link="http://paperless.test/api/documents/?page=3&page_size=1")
    reader = PaperlessDocumentReader(make_client(server))

    found = await reader.search_documents(DocumentQuery(), page=2, page_size=1)

    assert server.sent == [{"fields": FIELDS, "page": "2", "page_size": "1"}]
    assert "content" not in FIELDS.split(",")
    assert (found.count, found.next_page) == (1, 3)
    assert found.hits == (
        DocumentHit(
            id=1,
            title="e2e-public-invoice",
            created=date(2026, 11, 11),
            added=datetime(2026, 11, 11, 19, 47, 9, 49803, tzinfo=UTC),
            correspondent=1,
            document_type=1,
            storage_path=1,
            tags=(1, 3, 2),
            custom_fields=(
                CustomFieldValue(field=4, value=True),
                CustomFieldValue(field=8, value=[2]),
                CustomFieldValue(field=10, value="1sg5d8f1333w5Qak"),
            ),
        ),
    )


async def test_every_filter_becomes_its_paperless_parameter(make_client: ClientFactory) -> None:
    server = Documents()
    query = DocumentQuery(
        query="invoice",
        title_contains="e2e",
        correspondent=1,
        document_type=2,
        storage_path=3,
        tags_all=(1, 2),
        tags_any=(3,),
        tags_none=(4, 5),
        created_from=date(2026, 1, 1),
        created_to=date(2026, 12, 31),
        added_from=date(2026, 2, 1),
        added_to=date(2026, 2, 28),
        owner=7,
        inbox=False,
        custom_field_query=["due", "range", ["today", "in 30 days"]],
        ordering="-created",
    )

    _ = await PaperlessDocumentReader(make_client(server)).search_documents(
        query, page=1, page_size=25
    )

    [sent] = server.sent
    assert {name: value for name, value in sent.items() if name not in {"fields", "page"}} == {
        "query": "invoice",
        "title__icontains": "e2e",
        "correspondent__id": "1",
        "document_type__id": "2",
        "storage_path__id": "3",
        "tags__id__all": "1,2",
        "tags__id__in": "3",
        "tags__id__none": "4,5",
        "created__date__gte": "2026-01-01",
        "created__date__lte": "2026-12-31",
        "added__date__gte": "2026-02-01",
        "added__date__lte": "2026-02-28",
        "owner__id": "7",
        "is_in_inbox": "false",
        "custom_field_query": '["due", "range", ["today", "in 30 days"]]',
        "ordering": "-created",
        "page_size": "25",
    }


async def test_more_like_is_sent_as_more_like_id(make_client: ClientFactory) -> None:
    server = Documents()

    _ = await PaperlessDocumentReader(make_client(server)).search_documents(
        DocumentQuery(more_like=2), page=1, page_size=25
    )

    assert server.sent[0]["more_like_id"] == "2"


async def test_search_hits_carry_highlights_and_empty_ones_are_none(
    make_client: ClientFactory,
) -> None:
    server = Documents(
        {**ROW, "__search_hit__": HIT},
        {**ROW, "id": 2, "__search_hit__": {**HIT, "highlights": ""}},
    )

    found = await PaperlessDocumentReader(make_client(server)).search_documents(
        DocumentQuery(query="invoice"), page=1, page_size=25
    )

    assert [hit.highlights for hit in found.hits] == ["E2E public <b>invoice</b> ACME 2026", None]


async def test_a_match_in_a_note_only_highlights_the_note(make_client: ClientFactory) -> None:
    hit = {**HIT, "highlights": "", "note_highlights": "<b>paid</b> in March"}
    server = Documents({**ROW, "__search_hit__": hit})

    found = await PaperlessDocumentReader(make_client(server)).search_documents(
        DocumentQuery(query="notes.note:paid"), page=1, page_size=25
    )

    assert found.hits[0].highlights == "<b>paid</b> in March"


@pytest.mark.parametrize(
    ("status", "body", "query", "error"),
    [
        (403, "Invalid more_like_id", DocumentQuery(more_like=9), "No document 9"),
        (403, "Insufficient permissions.", DocumentQuery(more_like=9), "No document 9"),
        (404, '{"detail": "Invalid page."}', DocumentQuery(), "Page 4 is beyond"),
    ],
)
async def test_paperless_errors_of_a_search_name_the_cause(
    make_client: ClientFactory, status: int, body: str, query: DocumentQuery, error: str
) -> None:
    def answer(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, text=body, headers={"Content-Type": "text/html"})

    with pytest.raises(NotFound, match=error) as raised:
        _ = await PaperlessDocumentReader(make_client(answer)).search_documents(
            query, page=4, page_size=25
        )

    assert raised.value.hint


async def test_a_403_without_more_like_stays_permission_denied(make_client: ClientFactory) -> None:
    def forbidden(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(403, json={"detail": "You do not have permission."})

    with pytest.raises(PermissionDenied):
        _ = await PaperlessDocumentReader(make_client(forbidden)).search_documents(
            DocumentQuery(), page=1, page_size=25
        )


async def test_a_rejected_custom_field_query_is_a_validation_error(
    make_client: ClientFactory,
) -> None:
    def rejects(_request: httpx2.Request) -> httpx2.Response:
        body = {"custom_field_query": {"0": ["'nope' is not a valid custom field."]}}
        return httpx2.Response(400, json=body)

    reader = PaperlessDocumentReader(make_client(rejects))

    with pytest.raises(Validation, match="'nope' is not a valid custom field"):
        _ = await reader.search_documents(
            DocumentQuery(custom_field_query=["nope", "exact", "x"]), page=1, page_size=25
        )


# Responses recorded from the 3.4.2 E2E stack (GET /api/documents/1/ and its sub-resources).
DETAIL: Json = {
    "id": 1,
    "correspondent": 1,
    "document_type": 1,
    "storage_path": 1,
    "title": "e2e-public-invoice",
    "tags": [1, 3, 2],
    "created": "2026-11-12",
    "modified": "2026-11-12T17:49:42.862350Z",
    "added": "2026-11-12T17:44:34.261817Z",
    "archive_serial_number": 2,
    "original_file_name": "e2e-public-invoice.pdf",
    "archived_file_name": "2026-11-12 e2e-acme e2e-public-invoice.pdf",
    "owner": None,
    "notes": [
        {
            "id": 1,
            "note": "e2e remark zebra",
            "created": "2026-11-12T17:49:42.834036Z",
            "user": {"id": 2, "username": "admin", "first_name": "", "last_name": ""},
        }
    ],
    "custom_fields": [{"value": "hello", "field": 1}, {"value": [2], "field": 8}],
    "page_count": 1,
    "mime_type": "application/pdf",
    "versions": [
        {
            "id": 1,
            "added": "2026-11-12T17:44:34.261817Z",
            "version_label": None,
            "checksum": "c7d35dab",
            "is_root": True,
        }
    ],
}
METADATA: Json = {
    "original_checksum": "c7d35dab",
    "original_size": 644,
    "original_mime_type": "application/pdf",
    "media_filename": "e2e/2026/e2e-public-invoice.pdf",
    "has_archive_version": True,
    "original_metadata": [],
    "archive_checksum": "4ea35f9e",
    "archive_media_filename": "e2e/2026/e2e-public-invoice.pdf",
    "original_filename": "e2e-public-invoice.pdf",
    "archive_size": 9138,
    "archive_metadata": [
        {
            "namespace": "http://ns.adobe.com/pdf/1.3/",
            "prefix": "pdf",
            "key": "Producer",
            "value": "x",
        }
    ],
    "barcodes": [],
    "lang": "ro",
}
HISTORY: list[Json] = [
    {
        "id": 55,
        "timestamp": "2026-11-12T17:49:42.838903Z",
        "action": "update",
        "changes": {"Note Added": ["None", 1]},
        "actor": None,
    },
    {
        "id": 17,
        "timestamp": "2026-11-12T17:44:34.265397Z",
        "action": "create",
        "changes": {
            "title": ["None", "e2e-public-invoice"],
            "content": ["None", "E2E public invoice ACME 2026"],
        },
        "actor": {"id": 2, "username": "admin"},
    },
]


class Resource:
    """Answers every GET with one body and records the paths and query parameters sent."""

    def __init__(self, body: object, status: int = 200) -> None:
        self.body: object = body
        self.status: int = status
        self.sent: list[tuple[str, dict[str, str]]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.sent.append((request.url.path, dict(request.url.params)))
        return httpx2.Response(self.status, json=self.body)


async def test_a_document_is_fetched_by_id_with_named_fields_and_no_content(
    make_client: ClientFactory,
) -> None:
    server = Resource(DETAIL)

    record = await PaperlessDocumentReader(make_client(server)).get_document(1)

    [(path, params)] = server.sent
    assert path == "/api/documents/1/"
    assert set(params) == {"fields"}
    assert "content" not in params["fields"].split(",")
    assert (record.id, record.title, record.created) == (
        1,
        "e2e-public-invoice",
        date(2026, 11, 12),
    )
    assert record.modified == datetime(2026, 11, 12, 17, 49, 42, 862350, tzinfo=UTC)
    assert (record.correspondent, record.document_type, record.storage_path) == (1, 1, 1)
    assert record.tags == (1, 3, 2)
    assert record.custom_fields == (
        CustomFieldValue(field=1, value="hello"),
        CustomFieldValue(field=8, value=[2]),
    )
    assert (record.archive_serial_number, record.owner, record.page_count) == (2, None, 1)
    assert (record.original_file_name, record.mime_type) == (
        "e2e-public-invoice.pdf",
        "application/pdf",
    )
    assert record.notes_count == 1
    [version] = record.versions
    assert (version.id, version.version_label, version.is_root) == (1, None, True)


async def test_a_missing_or_hidden_document_is_not_found_with_a_hint(
    make_client: ClientFactory,
) -> None:
    server = Resource({"detail": "No Document matches the given query."}, status=404)
    reader = PaperlessDocumentReader(make_client(server))

    with pytest.raises(NotFound, match="No document 9 exists that this user may see") as raised:
        _ = await reader.get_document(9)

    assert raised.value.hint
    assert "search_documents" in raised.value.hint


async def test_a_user_without_the_document_permission_is_denied(make_client: ClientFactory) -> None:
    server = Resource({"detail": "You do not have permission to perform this action."}, 403)

    with pytest.raises(PermissionDenied):
        _ = await PaperlessDocumentReader(make_client(server)).get_document(1)


async def test_the_content_is_asked_for_alone(make_client: ClientFactory) -> None:
    server = Resource({"content": "E2E public invoice ACME 2026"})

    text = await PaperlessDocumentReader(make_client(server)).get_document_content(1)

    assert text == "E2E public invoice ACME 2026"
    assert server.sent == [("/api/documents/1/", {"fields": "content"})]


async def test_the_content_of_a_missing_document_is_not_found(make_client: ClientFactory) -> None:
    server = Resource({"detail": "No Document matches the given query."}, status=404)

    with pytest.raises(NotFound, match="No document 9"):
        _ = await PaperlessDocumentReader(make_client(server)).get_document_content(9)


async def test_notes_carry_the_username_of_their_author(make_client: ClientFactory) -> None:
    server = Resource(DETAIL["notes"])

    notes = await PaperlessDocumentReader(make_client(server)).list_document_notes(1)

    assert server.sent == [("/api/documents/1/notes/", {})]
    [note] = notes
    assert (note.id, note.note, note.user) == (1, "e2e remark zebra", "admin")
    assert note.created == datetime(2026, 11, 12, 17, 49, 42, 834036, tzinfo=UTC)


type Call = Callable[[PaperlessDocumentReader, int], Awaitable[object]]

SUB_RESOURCES: list[tuple[Call, str]] = [
    (lambda reader, number: reader.list_document_notes(number), "notes"),
    (lambda reader, number: reader.list_document_history(number), "history"),
    (lambda reader, number: reader.get_document_metadata(number), "metadata"),
    (lambda reader, number: reader.get_document_suggestions(number), "suggestions"),
]


@pytest.mark.parametrize(("call", "path"), SUB_RESOURCES, ids=[path for _, path in SUB_RESOURCES])
async def test_a_sub_resource_of_a_missing_document_is_not_found(
    make_client: ClientFactory, call: Call, path: str
) -> None:
    server = Resource({"detail": "Not found."}, status=404)
    reader = PaperlessDocumentReader(make_client(server))

    with pytest.raises(NotFound, match="No document 9"):
        _ = await call(reader, 9)

    assert server.sent[0][0] == f"/api/documents/9/{path}/"


@pytest.mark.parametrize(("call", "path"), SUB_RESOURCES, ids=[path for _, path in SUB_RESOURCES])
async def test_a_sub_resource_the_user_may_not_read_is_permission_denied(
    make_client: ClientFactory, call: Call, path: str
) -> None:
    def answer(_request: httpx2.Request) -> httpx2.Response:
        if path == "notes":  # a DRF permission class answers JSON, the views text/html
            return httpx2.Response(403, json={"detail": "You do not have permission."})
        return httpx2.Response(
            403, text="Insufficient permissions", headers={"Content-Type": "text/html"}
        )

    with pytest.raises(PermissionDenied):
        _ = await call(PaperlessDocumentReader(make_client(answer)), 1)


async def test_the_history_never_holds_the_ocr_text_and_keeps_system_changes(
    make_client: ClientFactory,
) -> None:
    server = Resource(HISTORY)

    entries = await PaperlessDocumentReader(make_client(server)).list_document_history(1)

    assert server.sent == [("/api/documents/1/history/", {})]
    assert [entry.id for entry in entries] == [55, 17]
    assert (entries[0].actor, entries[1].actor) == (None, "admin")
    assert entries[0].changes == {"Note Added": ["None", 1]}
    assert entries[1].changes == {"title": ["None", "e2e-public-invoice"]}
    assert "ACME" not in repr(entries)


async def test_the_file_metadata_keeps_the_facts_and_drops_the_embedded_metadata(
    make_client: ClientFactory,
) -> None:
    server = Resource(METADATA)

    metadata = await PaperlessDocumentReader(make_client(server)).get_document_metadata(1)

    assert server.sent == [("/api/documents/1/metadata/", {})]
    assert metadata.model_dump() == {
        "original_filename": "e2e-public-invoice.pdf",
        "original_mime_type": "application/pdf",
        "original_size": 644,
        "original_checksum": "c7d35dab",
        "media_filename": "e2e/2026/e2e-public-invoice.pdf",
        "has_archive_version": True,
        "archive_size": 9138,
        "archive_checksum": "4ea35f9e",
        "archive_media_filename": "e2e/2026/e2e-public-invoice.pdf",
    }


async def test_a_document_without_archive_version_has_null_archive_facts(
    make_client: ClientFactory,
) -> None:
    body = {
        **METADATA,
        "has_archive_version": False,
        "archive_checksum": None,
        "archive_media_filename": None,
        "archive_size": None,
        "archive_metadata": None,
    }

    metadata = await PaperlessDocumentReader(make_client(Resource(body))).get_document_metadata(1)

    assert (metadata.archive_size, metadata.archive_checksum) == (None, None)


async def test_the_suggestions_are_ids_and_dates(make_client: ClientFactory) -> None:
    body = {
        "correspondents": [1],
        "tags": [2, 3],
        "document_types": [],
        "storage_paths": [4],
        "dates": ["2026-01-15"],
    }
    server = Resource(body)

    found = await PaperlessDocumentReader(make_client(server)).get_document_suggestions(1)

    assert server.sent == [("/api/documents/1/suggestions/", {})]
    assert found.model_dump() == {
        "correspondents": (1,),
        "tags": (2, 3),
        "document_types": (),
        "storage_paths": (4,),
        "dates": ("2026-01-15",),
    }


async def test_a_disabled_audit_log_is_a_feature_error(make_client: ClientFactory) -> None:
    def answer(_request: httpx2.Request) -> httpx2.Response:
        # 3.4.2 `history`: HttpResponseBadRequest("Audit log is disabled")
        return httpx2.Response(
            400, text="Audit log is disabled", headers={"Content-Type": "text/html"}
        )

    with pytest.raises(FeatureUnavailable, match="audit log is disabled") as raised:
        _ = await PaperlessDocumentReader(make_client(answer)).list_document_history(1)

    assert raised.value.hint


async def test_titles_are_asked_for_by_id_in_one_request(make_client: ClientFactory) -> None:
    server = Documents({"id": 1, "title": "one"}, {"id": 2, "title": "two"})

    titles = await PaperlessDocumentReader(make_client(server)).get_document_titles([1, 2, 1])

    assert titles == {1: "one", 2: "two"}
    assert server.sent == [{"id__in": "1,2", "fields": "id,title", "page_size": "2"}]


async def test_no_titles_are_asked_for_without_ids(make_client: ClientFactory) -> None:
    server = Documents()

    assert await PaperlessDocumentReader(make_client(server)).get_document_titles([]) == {}
    assert server.sent == []


WEBP = b"RIFF\x1a\x00\x00\x00WEBPVP8L"
PDF = b"%PDF-1.4 recorded"
# Content-Disposition headers recorded from Paperless 3.4.2: `filename` is ASCII-folded (the
# non-ASCII characters are dropped), `filename*` (RFC 5987) holds the real name.
THUMB_DISPOSITION = 'inline; filename="0000030.webp"'
NON_ASCII_DISPOSITION = (
    'attachment; filename="2026-11-12 probe-Uni  .pdf"; '
    "filename*=utf-8''2026-11-12%20probe-%C3%9Cn%C3%AF%20%E6%97%A5%E6%9C%AC%E8%AA%9E%20%E2%82%AC.pdf"
)


class Binary:
    """Answers every GET with a file and records the paths and query parameters sent."""

    def __init__(self, content: bytes, content_type: str, disposition: str | None) -> None:
        self.content: bytes = content
        self.headers: dict[str, str] = {"Content-Type": content_type}
        if disposition is not None:
            self.headers["Content-Disposition"] = disposition
        self.sent: list[tuple[str, dict[str, str]]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.sent.append((request.url.path, dict(request.url.params)))
        return httpx2.Response(200, content=self.content, headers=self.headers)


async def test_a_thumbnail_is_fetched_as_the_image_paperless_sends(
    make_client: ClientFactory,
) -> None:
    server = Binary(WEBP, "image/webp", THUMB_DISPOSITION)

    found = await PaperlessDocumentReader(make_client(server)).get_document_thumbnail(30)

    assert server.sent == [("/api/documents/30/thumb/", {})]
    assert found == DocumentFile(content=WEBP, mime_type="image/webp", filename="0000030.webp")


@pytest.mark.parametrize(("version", "original"), [("original", "true"), ("archive", "false")])
async def test_a_version_is_chosen_with_the_original_parameter(
    make_client: ClientFactory, version: Literal["original", "archive"], original: str
) -> None:
    server = Binary(PDF, "application/pdf", NON_ASCII_DISPOSITION)

    found = await PaperlessDocumentReader(make_client(server)).get_document_file(
        30, version, max_bytes=1000
    )

    assert server.sent == [("/api/documents/30/download/", {"original": original})]
    assert found.content == PDF


async def test_the_filename_is_the_rfc_5987_one_with_its_non_ascii_characters(
    make_client: ClientFactory,
) -> None:
    server = Binary(PDF, "application/pdf", NON_ASCII_DISPOSITION)

    found = await PaperlessDocumentReader(make_client(server)).get_document_file(
        30, "original", max_bytes=1000
    )

    assert found.filename == "2026-11-12 probe-Ünï 日本語 €.pdf"


async def test_the_content_type_is_kept_with_its_parameters(make_client: ClientFactory) -> None:
    disposition = (
        "attachment; filename=\"2026-11-12 probe-txt-a.txt\"; filename*=utf-8''x%C3%A4.txt"
    )
    server = Binary(b"plain text probe", "text/plain; charset=utf-8", disposition)

    found = await PaperlessDocumentReader(make_client(server)).get_document_file(
        31, "original", max_bytes=1000
    )

    assert (found.mime_type, found.filename) == ("text/plain; charset=utf-8", "xä.txt")


FILES: list[tuple[Call, str]] = [
    (lambda reader, number: reader.get_document_thumbnail(number), "thumb"),
    (
        lambda reader, number: reader.get_document_file(number, "original", max_bytes=1000),
        "download",
    ),
]


@pytest.mark.parametrize(("call", "path"), FILES, ids=[path for _, path in FILES])
async def test_the_file_of_a_missing_document_is_not_found(
    make_client: ClientFactory, call: Call, path: str
) -> None:
    server = Resource({"detail": "Not found."}, status=404)

    with pytest.raises(NotFound, match="No document 9") as raised:
        _ = await call(PaperlessDocumentReader(make_client(server)), 9)

    assert raised.value.hint
    assert server.sent[0][0] == f"/api/documents/9/{path}/"


@pytest.mark.parametrize(("call", "path"), FILES, ids=[path for _, path in FILES])
async def test_the_file_of_a_hidden_document_is_permission_denied(
    make_client: ClientFactory, call: Call, path: str
) -> None:
    def answer(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == f"/api/documents/2/{path}/"
        # 3.4.2: HttpResponseForbidden for a document the user may not see, text/html
        return httpx2.Response(
            403, text="Insufficient permissions", headers={"Content-Type": "text/html"}
        )

    with pytest.raises(PermissionDenied):
        _ = await call(PaperlessDocumentReader(make_client(answer)), 2)


@pytest.mark.parametrize(("call", "path"), FILES, ids=[path for _, path in FILES])
async def test_a_user_without_the_document_permission_is_denied_a_file(
    make_client: ClientFactory, call: Call, path: str
) -> None:
    del path
    server = Resource({"detail": "You do not have permission to perform this action."}, 403)

    with pytest.raises(PermissionDenied):
        _ = await call(PaperlessDocumentReader(make_client(server)), 1)


async def test_a_file_over_the_limit_is_truncated_but_still_named_and_typed(
    make_client: ClientFactory,
) -> None:
    server = Binary(PDF, "application/pdf", NON_ASCII_DISPOSITION)

    found = await PaperlessDocumentReader(make_client(server)).get_document_file(
        30, "original", max_bytes=len(PDF) - 1
    )

    assert found == DocumentFile(
        content=b"",
        mime_type="application/pdf",
        filename="2026-11-12 probe-Ünï 日本語 €.pdf",
        truncated=True,
    )


async def test_a_file_exactly_at_the_limit_is_whole(make_client: ClientFactory) -> None:
    server = Binary(PDF, "application/pdf", NON_ASCII_DISPOSITION)

    found = await PaperlessDocumentReader(make_client(server)).get_document_file(
        30, "original", max_bytes=len(PDF)
    )

    assert (found.content, found.truncated) == (PDF, False)


async def test_a_thumbnail_larger_than_any_real_one_is_refused(make_client: ClientFactory) -> None:
    server = Binary(b"x" * (THUMBNAIL_LIMIT + 1), "image/webp", THUMB_DISPOSITION)

    with pytest.raises(PaperlessMcpError, match="unexpected response"):
        _ = await PaperlessDocumentReader(make_client(server)).get_document_thumbnail(30)


# --- Mutation-testing additions (PLMCP-21)


async def test_the_not_found_errors_of_a_search_say_what_to_do_next(
    make_client: ClientFactory,
) -> None:
    def past_the_end(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(404, json={"detail": "Invalid page."})

    reader = PaperlessDocumentReader(make_client(past_the_end))
    with pytest.raises(NotFound) as raised:
        _ = await reader.search_documents(DocumentQuery(), page=4, page_size=25)

    assert raised.value.message == "Page 4 is beyond the last page of the results."
    assert raised.value.hint == "Search with page=1; count and next_page tell how far it goes."


async def test_a_disabled_audit_log_is_explained_word_for_word(make_client: ClientFactory) -> None:
    def answer(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            400, text="Audit log is disabled", headers={"Content-Type": "text/html"}
        )

    with pytest.raises(FeatureUnavailable) as raised:
        _ = await PaperlessDocumentReader(make_client(answer)).list_document_history(1)

    assert raised.value.message == "The Paperless audit log is disabled on this instance."
    assert raised.value.hint == "Leave history out of include."


async def test_a_note_whose_author_was_deleted_has_no_user() -> None:
    # Paperless sets the author to null when the user is deleted, though its schema says the
    # field is never null: the plain transport does not hold the response to the schema.
    orphan = {"id": 2, "note": "left behind", "created": "2026-11-12T17:49:42Z", "user": None}
    client, _ = streaming(lambda _: httpx2.Response(200, json=[orphan]))

    [note] = await PaperlessDocumentReader(client).list_document_notes(1)

    assert (note.id, note.user) == (2, None)


async def test_a_download_without_headers_is_an_unnamed_binary_file() -> None:
    client, _ = streaming(lambda _: httpx2.Response(200, content=PDF))

    found = await PaperlessDocumentReader(client).get_document_file(1, "original", max_bytes=100)

    assert (found.mime_type, found.filename) == ("application/octet-stream", None)


async def test_the_owner_and_the_version_label_of_a_document_are_kept(
    make_client: ClientFactory,
) -> None:
    [version] = cast("list[Json]", DETAIL["versions"])
    detail = {**DETAIL, "owner": 2, "versions": [{**version, "version_label": "scan 2"}]}

    record = await PaperlessDocumentReader(make_client(Resource(detail))).get_document(1)

    assert record.owner == 2
    assert [v.version_label for v in record.versions] == ["scan 2"]
