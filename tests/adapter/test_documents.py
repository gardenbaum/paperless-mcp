"""PaperlessDocumentReader on responses recorded from the Paperless 3.4.2 E2E stack."""

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import httpx2
import pytest

from paperless_mcp.domain.documents import CustomFieldValue, DocumentHit, DocumentQuery
from paperless_mcp.domain.errors import NotFound, PermissionDenied, Validation
from paperless_mcp.paperless.documents import FIELDS, PaperlessDocumentReader

if TYPE_CHECKING:
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
