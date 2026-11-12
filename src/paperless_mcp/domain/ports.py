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

    from paperless_mcp.domain.ai import AiSuggestions, ChatAnswer
    from paperless_mcp.domain.documents import (
        DocumentFile,
        DocumentPage,
        DocumentPatch,
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

    async def get_document_file(
        self, document_id: int, version: FileVersion, *, max_bytes: int
    ) -> DocumentFile:
        """The original file or the archive version (the OCR'd PDF/A) of a document.

        Reads at most ``max_bytes`` bytes: a larger file comes back ``truncated`` with its type
        and name but no content. Paperless answers the original for ``archive`` when the
        document has no archive version, so callers check ``has_archive_version`` first.

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


class DocumentWriter(Protocol):
    """Changes to documents."""

    async def update_document(self, document_id: int, patch: DocumentPatch) -> DocumentRecord:
        """Change the fields ``patch`` sets and return the document as it is afterwards.

        Paperless replaces the tag and custom field lists as a whole: this port sends what it
        is given, so callers merge first. Nothing else changes.

        Raises:
            NotFound: No such document, or the user may not see it.
            PermissionDenied: The user may not change documents, or this one.
            Validation: Paperless rejected a value (unknown id, wrong type, duplicate ASN).
        """
        ...

    async def add_note(self, document_id: int, text: str) -> Note:
        """Add a note authored by the user and return it.

        Paperless answers with all notes of the document; the new one is the one with the
        highest id. It accepts empty and whitespace-only text, so callers check that.

        Raises:
            NotFound: No such document.
            PermissionDenied: The user lacks the note permission, may not see the document, or
                may not change it (a view grant is not enough for a note).
            Validation: Paperless could not save the note.
        """
        ...

    async def delete_note(self, document_id: int, note_id: int) -> None:
        """Delete one note of a document for good; notes have no trash.

        Raises:
            NotFound: No such document, or no such note on it.
            PermissionDenied: The user lacks the note permission or may not change the document.
        """
        ...

    async def delete_document(self, document_id: int) -> None:
        """Move a document to the Paperless trash; it can be restored from there.

        Raises:
            NotFound: No such document, or the user may not see it.
            PermissionDenied: The user may not delete documents, or this one (an owner, or any
                user for an unowned document; a change grant is not enough).
        """
        ...


class TaxonomyWriter(Protocol):
    """Changes to tags, correspondents and the like."""

    async def create_tag(self, name: str) -> TaxonomyItem:
        """A new tag named ``name`` that matches nothing automatically, owned by the user.

        Raises:
            PermissionDenied: The user may not add tags.
            Validation: The name is blank, too long, or the user already has such a tag.
        """
        ...


class AiReader(Protocol):
    """Paperless AI (a language model over the documents); it changes nothing in Paperless."""

    async def ask(self, question: str, *, document_id: int | None) -> ChatAnswer:
        """The whole answer of the chat to ``question``, about one document or all the user sees.

        The answer streams in Paperless; this returns it complete, cut at a size or time limit
        (``truncated``). Paperless names at most three source documents.

        Raises:
            FeatureUnavailable: Paperless AI is switched off.
            NotFound: ``document_id`` names no document.
            PermissionDenied: The user may not view documents, or not this document.
            UpstreamUnavailable: The language model failed or took too long.
        """
        ...

    async def get_ai_suggestions(self, document_id: int) -> AiSuggestions:
        """What the language model suggests for a document, asked for now (or cached by Paperless).

        Raises:
            NotFound: No such document.
            PermissionDenied: The user may not change the document (Paperless requires it).
            FeatureUnavailable: Paperless AI is switched off.
            UpstreamUnavailable: The language model failed or took too long.
        """
        ...
