"""DocumentReader on ``GET /api/documents/``.

Every search names its ``fields``, so Paperless never sends the OCR content, notes or
permissions. Full-text and more-like searches add ``__search_hit__`` to each row.
"""

import json
from datetime import date, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, JsonValue

from paperless_mcp.domain.documents import CustomFieldValue, DocumentHit, DocumentPage
from paperless_mcp.domain.errors import NotFound, PermissionDenied
from paperless_mcp.paperless.pagination import Page

if TYPE_CHECKING:
    from paperless_mcp.domain.documents import DocumentQuery
    from paperless_mcp.paperless.client import PaperlessClient, Params

PATH = "/api/documents/"
FIELDS = "id,title,created,added,correspondent,document_type,storage_path,tags,custom_fields"


class _Value(BaseModel):
    field: int
    value: JsonValue


class _Hit(BaseModel):
    highlights: str | None = None
    note_highlights: str | None = None


class _Row(BaseModel):
    id: int
    title: str
    created: date
    added: datetime
    correspondent: int | None
    document_type: int | None
    storage_path: int | None
    tags: list[int]
    custom_fields: list[_Value]
    search_hit: _Hit | None = Field(default=None, alias="__search_hit__")


def _csv(ids: tuple[int, ...]) -> str | None:
    return ",".join(map(str, ids)) or None


def _day(day: date | None) -> str | None:
    return day.isoformat() if day else None


def params(query: DocumentQuery) -> dict[str, str | int]:
    """The Paperless query parameters of ``query``; unset filters are left out."""
    candidates: dict[str, str | int | None] = {
        "query": query.query,
        "more_like_id": query.more_like,
        "title__icontains": query.title_contains,
        "correspondent__id": query.correspondent,
        "document_type__id": query.document_type,
        "storage_path__id": query.storage_path,
        "tags__id__all": _csv(query.tags_all),
        "tags__id__in": _csv(query.tags_any),
        "tags__id__none": _csv(query.tags_none),
        "created__date__gte": _day(query.created_from),
        "created__date__lte": _day(query.created_to),
        "added__date__gte": _day(query.added_from),
        "added__date__lte": _day(query.added_to),
        "owner__id": query.owner,
        "is_in_inbox": None if query.inbox is None else str(query.inbox).lower(),
        "custom_field_query": (
            None if query.custom_field_query is None else json.dumps(query.custom_field_query)
        ),
        "ordering": query.ordering,
    }
    return {name: value for name, value in candidates.items() if value is not None}


def unseen_document(document: int) -> NotFound:
    """The error for a ``more_like`` document that is missing or hidden from the user."""
    message = f"No document {document} exists that this user may see."
    return NotFound(message, hint="Take the id from a search_documents row.")


def beyond_last_page(page: int) -> NotFound:
    """The error for a page past the end of the results."""
    message = f"Page {page} is beyond the last page of the results."
    return NotFound(message, hint="Search with page=1; count and next_page tell how far it goes.")


def _hit(row: _Row) -> DocumentHit:
    return DocumentHit(
        id=row.id,
        title=row.title,
        created=row.created,
        added=row.added,
        correspondent=row.correspondent,
        document_type=row.document_type,
        storage_path=row.storage_path,
        tags=tuple(row.tags),
        custom_fields=tuple(
            CustomFieldValue(field=v.field, value=v.value) for v in row.custom_fields
        ),
        highlights=(
            (row.search_hit.highlights or row.search_hit.note_highlights or None)
            if row.search_hit
            else None
        ),
    )


class PaperlessDocumentReader:
    """``DocumentReader`` over the Paperless REST API."""

    def __init__(self, client: PaperlessClient) -> None:
        """Read through ``client``."""
        self._client: PaperlessClient = client

    async def search_documents(
        self, query: DocumentQuery, *, page: int, page_size: int
    ) -> DocumentPage:
        """See ``DocumentReader.search_documents``.

        Paperless answers 403 to a ``more_like_id`` the user may not see or that does not
        exist, and 404 to a page beyond the last one.
        """
        sent: Params = {**params(query), "fields": FIELDS, "page": page, "page_size": page_size}
        try:
            found = await self._client.get(PATH, Page[_Row], params=sent)
        except PermissionDenied:
            if query.more_like is None:
                raise
            raise unseen_document(query.more_like) from None
        except NotFound:
            raise beyond_last_page(page) from None
        return DocumentPage(
            count=found.count,
            next_page=found.next_page,
            hits=tuple(_hit(row) for row in found.results),
        )
