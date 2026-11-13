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
    from collections.abc import Collection, Sequence

    from paperless_mcp.domain.ai import AiSuggestions, ChatAnswer
    from paperless_mcp.domain.bulk import BulkKind, DocumentChange, PermissionsChange
    from paperless_mcp.domain.documents import (
        DocumentFile,
        DocumentPage,
        DocumentPatch,
        DocumentQuery,
        DocumentRecord,
        DocumentUpload,
        DocumentVersion,
        FileMetadata,
        FileVersion,
        HistoryEntry,
        Note,
        Suggestions,
    )
    from paperless_mcp.domain.instance import InstanceOverview
    from paperless_mcp.domain.tasks import TaskStatus
    from paperless_mcp.domain.taxonomy import (
        TaxonomyDetail,
        TaxonomyItem,
        TaxonomyKind,
        TaxonomyValues,
        WritableKind,
    )
    from paperless_mcp.domain.versions import VersionUpload


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

    async def upload_document(self, upload: DocumentUpload) -> str:
        """Hand a file to Paperless for consumption and return the id of its task.

        Paperless checks the form, not the file: it answers at once with the task's UUID, and
        consumption happens later. A clash it only sees then (an archive serial number that is
        taken) fails the task, not this call. Uploading the same content twice creates two
        documents on 3.4.2. A busy Paperless is not retried: a retry could upload twice.

        Raises:
            PermissionDenied: The user may not add documents.
            Validation: Paperless rejected the form (unsupported file type, unknown id, bad
                custom field value).
        """
        ...

    async def upload_document_version(self, document_id: int, upload: VersionUpload) -> str:
        """Hand a file to Paperless as a new version of a document and return its task's id.

        Like ``upload_document``: Paperless checks the form and answers with the task's UUID,
        and consumption happens later. The task's document is the new version (not the root).
        The new version becomes the newest one. The same content twice makes two versions. Not
        retried: a retry could add the version twice.

        Raises:
            NotFound: No such document.
            PermissionDenied: The user lacks the global add and change permissions, or may not
                change the document (its owner, a superuser, anyone for an unowned document, or a
                user with a change grant): a document the user may not see is refused, not hidden.
            Validation: Paperless rejected the form (unsupported file type, label too long).
        """
        ...

    async def set_version_label(
        self, document_id: int, version_id: int, label: str | None
    ) -> DocumentVersion:
        """Set the label of a version (the root's too) and return the version; None clears it.

        Raises:
            NotFound: No such document, or it has no such version.
            PermissionDenied: The user lacks the global change permission or may not change the
                document (as for ``upload_document_version``).
            Validation: The label is longer than 64 characters.
        """
        ...

    async def delete_version(self, document_id: int, version_id: int) -> int:
        """Delete a version other than the root and return the id of the newest one left.

        Paperless moves the version to the trash, where it can be restored from (with its
        document id being the version id). Not retried: a repeat would answer not found.

        Raises:
            NotFound: No such document, or it has no such version.
            PermissionDenied: The user lacks the global delete permission, or is not the owner
                (a superuser, or anyone for an unowned document): a change grant is not enough.
            Validation: The version is the root, which only deleting the document removes.
        """
        ...

    async def merge_as_versions(
        self, root_id: int, source_ids: Sequence[int], label: str | None
    ) -> None:
        """Make the source documents versions of the root document.

        The sources stop existing as documents of their own, for good (no trash): their
        metadata, notes and tags are lost, only their files remain, as the root's newest
        versions. ``label`` is only valid with exactly one source. Paperless merges at once. Not
        retried: a repeat would answer 400 for sources that are gone.

        Raises:
            PermissionDenied: Unless the user is a superuser: the global change and delete
                permissions, and a change grant on, and ownership of (or no owner for), every
                document are needed; a document the user may not see is refused, not hidden.
            Validation: A document is missing or listed twice, a source has versions of its own
                or is itself a version, or ``label`` comes with several sources.
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

    async def create_taxonomy_item(
        self, kind: WritableKind, name: str, values: TaxonomyValues
    ) -> TaxonomyDetail:
        """A new object of ``kind`` named ``name`` with the settings ``values`` sets.

        The user owns it.

        Where the kind has a matching rule it defaults to "none" (never assigned automatically).
        ``values.name`` is ignored; a custom field needs ``data_type``, a storage path ``path``.

        Raises:
            PermissionDenied: The user may not add objects of ``kind``.
            Validation: Paperless rejected a value (blank or duplicate name, bad colour, unknown
                parent, invalid pattern or path template, missing options).
        """
        ...

    async def update_taxonomy_item(
        self, kind: WritableKind, item_id: int, values: TaxonomyValues
    ) -> TaxonomyDetail:
        """Change the settings ``values`` sets and return the object as it is afterwards.

        Select options in ``values.select_options`` replace the old list; options with an id keep
        it. A custom field's options stay when ``values`` leaves them out (the adapter sends
        them along, Paperless refuses a change of a select field without them).

        Raises:
            NotFound: No such object, or the user may not see it.
            PermissionDenied: The user may not change objects of ``kind``, or this one.
            Validation: Paperless rejected a value (a parent that would make a cycle, ...).
        """
        ...

    async def delete_taxonomy_item(self, kind: WritableKind, item_id: int) -> None:
        """Delete the object for good: Paperless has no trash for taxonomy.

        Documents lose what refers to it (tag, correspondent, type, path, or the custom field's
        value); deleting a tag deletes its child tags too, at any depth.

        Raises:
            NotFound: No such object, or the user may not see it.
            PermissionDenied: The user may not delete objects of ``kind``, or this one.
        """
        ...


class BulkWriter(Protocol):
    """Changes to many documents or taxonomy objects in one request.

    Paperless checks every selected object first and then changes all of them, or answers an
    error and changes none. Ids that do not exist, appear twice or are an empty list are
    rejected as a whole (callers deduplicate and check before they ask).
    """

    async def bulk_edit_documents(
        self, document_ids: Sequence[int], change: DocumentChange
    ) -> None:
        """Apply ``change`` to every document of ``document_ids``.

        Tags are removed (with their child tags) and then added (with their parent tags); custom
        fields are set one by one and the others stay. A deletion moves to the trash;
        reprocessing is queued and runs in the background.

        Raises:
            PermissionDenied: The user may not change one of the documents (nothing is changed),
                or may not delete it: that needs ownership.
            Validation: An id or a referenced object does not exist, or a value is wrong; the
                message names the reason and nothing is changed.
        """
        ...

    async def delete_taxonomy_items(self, kind: BulkKind, item_ids: Sequence[int]) -> None:
        """Delete the objects for good; documents lose what refers to them, tags take children.

        Raises:
            PermissionDenied: The user may not delete one of them (an owner, or any user for an
                unowned object, and the global permission).
            Validation: An id does not exist; nothing is changed.
        """
        ...

    async def set_taxonomy_permissions(
        self, kind: BulkKind, item_ids: Sequence[int], change: PermissionsChange
    ) -> None:
        """Set the owner and/or the grants of the objects.

        Raises:
            PermissionDenied: The user may not change one of them (owner or unowned).
            Validation: An id, user or group does not exist; nothing is changed.
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
