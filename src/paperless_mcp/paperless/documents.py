"""DocumentReader on ``GET /api/documents/`` and DocumentWriter on ``PATCH /api/documents/{id}/``.

Every search names its ``fields``, so Paperless never sends the OCR content, notes or
permissions. Full-text and more-like searches add ``__search_hit__`` to each row.
"""

import json
from datetime import date, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, JsonValue, RootModel

from paperless_mcp.domain.documents import (
    CustomFieldValue,
    DocumentFile,
    DocumentHit,
    DocumentPage,
    DocumentPatch,
    DocumentRecord,
    DocumentVersion,
    FileMetadata,
    HistoryEntry,
    Note,
    Suggestions,
)
from paperless_mcp.domain.errors import (
    FeatureUnavailable,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    Validation,
)
from paperless_mcp.paperless.disposition import filename
from paperless_mcp.paperless.pagination import Page

if TYPE_CHECKING:
    from collections.abc import Collection

    from paperless_mcp.domain.documents import DocumentQuery, FileVersion
    from paperless_mcp.paperless.client import PaperlessClient, Params

PATH = "/api/documents/"
# Most bytes of a thumbnail read; real ones are 2 KB to 30 KB WebP, so this is generous.
THUMBNAIL_LIMIT = 4 * 1024 * 1024
FIELDS = "id,title,created,added,correspondent,document_type,storage_path,tags,custom_fields"
# A single document without its content (the OCR text can be a million characters).
DETAIL_FIELDS = (
    "id,title,created,added,modified,archive_serial_number,owner,correspondent,document_type,"
    "storage_path,tags,custom_fields,original_file_name,mime_type,page_count,"
    "notes,versions,user_can_change"
)


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


class _Version(BaseModel):
    id: int
    added: datetime
    version_label: str | None = None
    is_root: bool


class _User(BaseModel):
    username: str


class _Note(BaseModel):
    id: int
    note: str
    created: datetime | None = None
    user: _User | None = None


class _Detail(BaseModel):
    id: int
    title: str
    created: date
    added: datetime
    modified: datetime
    archive_serial_number: int | None
    owner: int | None
    correspondent: int | None
    document_type: int | None
    storage_path: int | None
    tags: list[int]
    custom_fields: list[_Value]
    original_file_name: str | None
    mime_type: str
    page_count: int | None
    notes: list[_Note]
    versions: list[_Version]


class _Readable(_Detail):
    """What a read of one document sends beyond an update's answer."""

    user_can_change: bool


class _Title(BaseModel):
    id: int
    title: str


class _Content(BaseModel):
    content: str


class _HistoryRow(BaseModel):
    id: int
    timestamp: datetime
    action: str
    changes: dict[str, JsonValue]
    actor: _User | None = None


class _Metadata(BaseModel):
    original_filename: str
    original_mime_type: str
    original_size: int
    original_checksum: str
    media_filename: str
    has_archive_version: bool
    archive_size: int | None = None
    archive_checksum: str | None = None
    archive_media_filename: str | None = None


class _Suggestions(BaseModel):
    correspondents: list[int]
    tags: list[int]
    document_types: list[int]
    storage_paths: list[int]
    dates: list[str]


class _Notes(RootModel[list[_Note]]):
    pass


class _History(RootModel[list[_HistoryRow]]):
    pass


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


def _note(row: _Note) -> Note:
    return Note(
        id=row.id, note=row.note, created=row.created, user=row.user.username if row.user else None
    )


def _entry(row: _HistoryRow) -> HistoryEntry:
    """An audit log entry; ``content`` holds the OCR text, which never leaves the adapter."""
    return HistoryEntry(
        id=row.id,
        timestamp=row.timestamp,
        action=row.action,
        actor=row.actor.username if row.actor else None,
        changes={name: change for name, change in row.changes.items() if name != "content"},
    )


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


def _record(row: _Detail, *, can_change: bool | None) -> DocumentRecord:
    return DocumentRecord(
        id=row.id,
        title=row.title,
        created=row.created,
        added=row.added,
        modified=row.modified,
        archive_serial_number=row.archive_serial_number,
        owner=row.owner,
        correspondent=row.correspondent,
        document_type=row.document_type,
        storage_path=row.storage_path,
        tags=tuple(row.tags),
        custom_fields=tuple(
            CustomFieldValue(field=v.field, value=v.value) for v in row.custom_fields
        ),
        original_file_name=row.original_file_name,
        mime_type=row.mime_type,
        page_count=row.page_count,
        notes_count=len(row.notes),
        versions=tuple(
            DocumentVersion(
                id=v.id, added=v.added, version_label=v.version_label, is_root=v.is_root
            )
            for v in row.versions
        ),
        user_can_change=can_change,
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

    async def get_document(self, document_id: int) -> DocumentRecord:
        """See ``DocumentReader.get_document``.

        Paperless answers 404 to a document that does not exist and to one the user may not see.
        """
        row = await self._one(document_id, "", _Readable, {"fields": DETAIL_FIELDS})
        return _record(row, can_change=row.user_can_change)

    async def get_document_content(self, document_id: int) -> str:
        """See ``DocumentReader.get_document_content``."""
        return (await self._one(document_id, "", _Content, {"fields": "content"})).content

    async def list_document_notes(self, document_id: int) -> tuple[Note, ...]:
        """See ``DocumentReader.list_document_notes``."""
        found = await self._one(document_id, "notes/", _Notes)
        return tuple(_note(row) for row in found.root)

    async def list_document_history(self, document_id: int) -> tuple[HistoryEntry, ...]:
        """See ``DocumentReader.list_document_history``.

        The endpoint answers a bare list although the schema says paginated, and 400 "Audit log
        is disabled" when the instance runs with PAPERLESS_AUDIT_LOG_ENABLED=false (the only
        400 this endpoint has).
        """
        try:
            found = await self._one(document_id, "history/", _History)
        except Validation:
            message = "The Paperless audit log is disabled on this instance."
            raise FeatureUnavailable(message, hint="Leave history out of include.") from None
        return tuple(_entry(row) for row in found.root)

    async def get_document_metadata(self, document_id: int) -> FileMetadata:
        """See ``DocumentReader.get_document_metadata``."""
        row = await self._one(document_id, "metadata/", _Metadata)
        return FileMetadata.model_validate(row.model_dump())

    async def get_document_suggestions(self, document_id: int) -> Suggestions:
        """See ``DocumentReader.get_document_suggestions``."""
        row = await self._one(document_id, "suggestions/", _Suggestions)
        return Suggestions(
            correspondents=tuple(row.correspondents),
            tags=tuple(row.tags),
            document_types=tuple(row.document_types),
            storage_paths=tuple(row.storage_paths),
            dates=tuple(row.dates),
        )

    async def get_document_thumbnail(self, document_id: int) -> DocumentFile:
        """See ``DocumentReader.get_document_thumbnail``.

        Paperless 3.4 sends WebP. A document the user may not see is answered 403 (text/html),
        a missing one 404, as for the other sub-resources.
        """
        found = await self._file(document_id, "thumb/", THUMBNAIL_LIMIT)
        if found.truncated:
            message = f"Paperless sent an unexpected response for the thumbnail of {document_id}."
            raise PaperlessMcpError(message)
        return found

    async def get_document_file(
        self, document_id: int, version: FileVersion, *, max_bytes: int
    ) -> DocumentFile:
        """See ``DocumentReader.get_document_file``.

        ``original=false`` is the archive version; Paperless answers the original for it when
        the document has none. The file name is the formatted one Paperless proposes (date,
        correspondent and title), not the name of the upload.
        """
        return await self._file(
            document_id,
            "download/",
            max_bytes,
            {"original": "true" if version == "original" else "false"},
        )

    async def get_document_titles(self, document_ids: Collection[int]) -> dict[int, str]:
        """See ``DocumentReader.get_document_titles``; ``id__in`` leaves hidden ids out."""
        wanted = sorted(set(document_ids))
        if not wanted:
            return {}
        sent: Params = {
            "id__in": ",".join(map(str, wanted)),
            "fields": "id,title",
            "page_size": len(wanted),
        }
        found = await self._client.get(PATH, Page[_Title], params=sent)
        return {row.id: row.title for row in found.results}

    async def _file(
        self, document_id: int, tail: str, max_bytes: int, params: Params | None = None
    ) -> DocumentFile:
        try:
            found = await self._client.download(
                f"{PATH}{document_id}/{tail}", params=params, max_bytes=max_bytes
            )
        except NotFound:
            raise unseen_document(document_id) from None
        return DocumentFile(
            content=found.content,
            mime_type=found.headers.get("Content-Type", "application/octet-stream"),
            filename=filename(found.headers.get("Content-Disposition")),
            truncated=found.truncated,
        )

    async def _one[M: BaseModel](
        self, document_id: int, tail: str, model: type[M], params: Params | None = None
    ) -> M:
        try:
            return await self._client.get(f"{PATH}{document_id}/{tail}", model, params=params)
        except NotFound:
            raise unseen_document(document_id) from None


class PaperlessDocumentWriter:
    """``DocumentWriter`` over the Paperless REST API."""

    def __init__(self, client: PaperlessClient) -> None:
        """Write through ``client``."""
        self._client: PaperlessClient = client

    async def update_document(self, document_id: int, patch: DocumentPatch) -> DocumentRecord:
        """See ``DocumentWriter.update_document``.

        Sends only the fields ``patch`` sets. The answer to a PATCH is the whole document, OCR
        text included, unless ``fields`` limits it (it does on 3.4.2, although the schema does
        not declare the parameter). Paperless answers 404 to a document that does not exist
        or that the user may not see, and 403 to a user without the change permission.
        """
        body = patch.model_dump(mode="json", exclude_unset=True)
        try:
            row = await self._client.call(
                "PATCH",
                f"{PATH}{document_id}/",
                _Detail,
                params={"fields": DETAIL_FIELDS},
                json=body,
            )
        except NotFound:
            raise unseen_document(document_id) from None
        return _record(row, can_change=None)
