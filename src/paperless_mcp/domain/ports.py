"""The ports: what services need from Paperless, split by resource into readers and writers.

Every Paperless port lives here. The adapter (``paperless_mcp.paperless``) and the test fake
implement them; the contract suite (tests/contract) needs a contract test per method and checks
that the fake guards every method of a ``*Writer`` port.

Hard rule: every method that changes anything in Paperless lives on a protocol named
``*Writer``, and ``*Reader`` protocols only read. The fake's write guard, and with it the
read-only tests of tools, find writes by that name alone.
"""

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Collection

    from paperless_mcp.domain.documents import (
        DocumentFile,
        DocumentPage,
        DocumentQuery,
        DocumentRecord,
        FileMetadata,
        FileVersion,
        HistoryEntry,
        Note,
        Suggestions,
    )
    from paperless_mcp.domain.instance import InstanceOverview
    from paperless_mcp.domain.tasks import TaskStatus
    from paperless_mcp.domain.taxonomy import TaxonomyItem, TaxonomyKind


class InstanceReader(Protocol):
    """Instance-wide facts as the current user sees them."""

    async def get_overview(self) -> InstanceOverview:
        """Versions, statistics, system status, current user and capabilities.

        What the user may not see is None in the result (see ``InstanceOverview``).
        """
        ...


class TaxonomyReader(Protocol):
    """Tags, correspondents, document types, storage paths, custom fields, views, users, groups."""

    async def list_taxonomy(self, kind: TaxonomyKind) -> list[TaxonomyItem]:
        """Every object of ``kind`` the user may see, over all pages, in Paperless order.

        Raises:
            PermissionDenied: The user lacks the global view permission for ``kind``.
        """
        ...


class DocumentReader(Protocol):
    """Documents the current user may see."""

    async def search_documents(
        self, query: DocumentQuery, *, page: int, page_size: int
    ) -> DocumentPage:
        """One page of the documents matching ``query``, in Paperless order.

        Fetches that page only and never the content. Full-text and more-like results are
        ranked by relevance unless ``query.ordering`` is set, and carry highlights.

        Raises:
            Validation: Paperless rejected the query (e.g. a bad ``custom_field_query``).
            NotFound: ``page`` is beyond the last page.
        """
        ...

    async def get_document(self, document_id: int) -> DocumentRecord:
        """One document without its content.

        Raises:
            NotFound: No such document, or the user may not see it.
            PermissionDenied: The user lacks the global ``view_document`` permission.
        """
        ...

    async def get_document_content(self, document_id: int) -> str:
        """The whole OCR text of a document; services slice it.

        Raises:
            NotFound: No such document, or the user may not see it.
            PermissionDenied: The user lacks the global ``view_document`` permission.
        """
        ...

    async def list_document_notes(self, document_id: int) -> tuple[Note, ...]:
        """The notes of a document, in Paperless order.

        Raises:
            NotFound: No such document.
            PermissionDenied: The user may not view notes (a separate permission in Paperless).
        """
        ...

    async def list_document_history(self, document_id: int) -> tuple[HistoryEntry, ...]:
        """The audit log of a document, newest first, without the OCR text.

        Raises:
            NotFound: No such document.
            PermissionDenied: The user may not view the audit log (it needs ``view_logentry``
                and a document that is unowned or the user's own; a view grant is not enough).
            FeatureUnavailable: The instance has the audit log switched off.
        """
        ...

    async def get_document_metadata(self, document_id: int) -> FileMetadata:
        """The file facts of a document.

        Raises:
            NotFound: No such document.
            PermissionDenied: The user may not see the document.
        """
        ...

    async def get_document_suggestions(self, document_id: int) -> Suggestions:
        """The classifier's suggestions (ids) for a document.

        Raises:
            NotFound: No such document.
            PermissionDenied: The user may not see the document.
        """
        ...

    async def get_document_thumbnail(self, document_id: int) -> DocumentFile:
        """The thumbnail image of a document (WebP in Paperless 3.4).

        Raises:
            NotFound: No such document.
            PermissionDenied: The user may not see the document.
        """
        ...

    async def get_document_file(self, document_id: int, version: FileVersion) -> DocumentFile:
        """The original file or the archive version (the OCR'd PDF/A) of a document.

        Reads the whole file into memory; callers check its size first
        (``get_document_metadata``). Paperless answers the original for ``archive`` when the
        document has no archive version, so callers check ``has_archive_version`` too.

        Raises:
            NotFound: No such document.
            PermissionDenied: The user may not see the document.
        """
        ...

    async def get_document_titles(self, document_ids: Collection[int]) -> dict[int, str]:
        """The titles of those documents the user may see; missing and hidden ones are left out.

        One request for at most as many documents as ``document_ids`` holds distinct ids.
        """
        ...


class TaskReader(Protocol):
    """Background tasks (uploads, PDF operations) the current user may see."""

    async def get_task(self, task_id: str) -> TaskStatus:
        """The task Paperless knows under the Celery id ``task_id``.

        Raises:
            NotFound: No task with that id exists that the user may see.
            PermissionDenied: The user lacks the permission to view tasks.
        """
        ...
