"""File versions of a document: reading, labelling, and the two changes that need confirm.

Deleting a version moves it to the Paperless trash and can be undone there. Merging documents
as versions cannot: the merged documents vanish and keep only their files, so both go through
the confirmation flow (decision-6, decision-15). Everything Paperless would refuse with a 400 and
this server can see beforehand is refused first, so the caller is never asked to confirm what
cannot happen.
"""

from typing import TYPE_CHECKING

from paperless_mcp.domain.errors import (
    ConfirmationRequired,
    NotFound,
    Validation,
    quoted,
)
from paperless_mcp.domain.versions import (
    DocumentVersions,
    VersionDeleted,
    VersionLabelSet,
    VersionsMerged,
)
from paperless_mcp.services.documents import document_link
from paperless_mcp.services.removal import RESTORE

if TYPE_CHECKING:
    from collections.abc import Sequence

    from paperless_mcp.domain.documents import DocumentRecord
    from paperless_mcp.domain.ports import DocumentReader, DocumentWriter

# Paperless's max_length of a version label.
MAX_LABEL = 64
# Most documents one merge takes: bounded, so the confirmation text and the reads stay small.
MAX_SOURCES = 20


def clean_label(label: str | None) -> str | None:
    """The label trimmed; None for none or blank (Paperless stores a blank label as none).

    Raises:
        Validation: The label is longer than ``MAX_LABEL`` characters.
    """
    text = (label or "").strip()
    if len(text) > MAX_LABEL:
        message = f"A version label holds at most {MAX_LABEL} characters; this one has {len(text)}."
        raise Validation(message, hint="Shorten the label.")
    return text or None


class VersionService:
    """Reads and changes the file versions of documents for the current user."""

    def __init__(self, reader: DocumentReader, writer: DocumentWriter, *, web_url: str) -> None:
        """Read through ``reader``, write through ``writer``; ``web_url`` is the Paperless UI."""
        self._reader: DocumentReader = reader
        self._writer: DocumentWriter = writer
        self._web_url: str = web_url

    async def list_versions(self, document_id: int) -> DocumentVersions:
        """The versions of a document, newest first.

        Raises:
            NotFound: No such document, or the user may not see it.
            PermissionDenied: The user may not view documents.
        """
        document = await self._reader.get_document(document_id)
        return DocumentVersions(
            document_id=document.id,
            title=document.title,
            current_version_id=document.versions[0].id,
            versions=document.versions,
            web_url=document_link(self._web_url, document.id),
        )

    async def set_label(self, document_id: int, version_id: int, label: str) -> VersionLabelSet:
        """Set the label of a version, the root's too; a blank ``label`` clears it.

        Raises:
            Validation: The label is longer than ``MAX_LABEL`` characters.
            NotFound: No such document, or it has no such version.
            PermissionDenied: The user may not change the document.
        """
        text = clean_label(label)
        version = await self._writer.set_version_label(document_id, version_id, text)
        return VersionLabelSet(
            document_id=document_id,
            version=version,
            web_url=document_link(self._web_url, document_id),
        )

    async def delete(
        self, document_id: int, version_id: int, *, confirm: bool = False
    ) -> VersionDeleted:
        """Delete a version other than the root; without ``confirm`` nothing changes.

        Raises:
            ConfirmationRequired: ``confirm`` is false. Nothing was changed.
            Validation: The version is the root.
            NotFound: No such document, or it has no such version.
            PermissionDenied: The user may not delete the document (not its owner).
        """
        document = await self._reader.get_document(document_id)
        found = [v for v in document.versions if v.id == version_id]
        if not found:
            message = f"Document {document_id} has no version {version_id}."
            raise NotFound(message, hint="List the versions with list_document_versions.")
        version = found[0]
        if version.is_root:
            message = (
                f"Version {version_id} is the root version, the original file of document "
                f"{document_id}."
            )
            raise Validation(
                message, hint="Delete the whole document with delete_document instead."
            )
        if not confirm:
            newest = document.versions[0]
            label = f" {quoted(version.version_label)}" if version.version_label else ""
            after = (
                f"the document's file and text then revert to version {document.versions[1].id}"
                if version.id == newest.id
                else f"version {newest.id} stays the newest"
            )
            effect = (
                f"delete version {version_id}{label} (added {version.added.date().isoformat()}) "
                f"of document {document_id} {quoted(document.title)}; it moves to the Paperless "
                f"trash and can be restored from there; {after}"
            )
            raise ConfirmationRequired.for_effect(effect)
        current_id = await self._writer.delete_version(document_id, version_id)
        return VersionDeleted(
            document_id=document_id,
            version_id=version_id,
            current_version_id=current_id,
            message=(
                f"Version {version_id} of document {document_id} is in the Paperless trash; "
                f"version {current_id} is the newest now. {RESTORE}"
            ),
        )

    async def merge(
        self,
        root_id: int,
        source_ids: Sequence[int],
        label: str | None,
        *,
        confirm: bool = False,
    ) -> VersionsMerged:
        """Merge documents into ``root_id`` as its newest versions.

        Without ``confirm`` nothing changes and the refusal says what would happen.

        The sources end up in the order given, the last one newest. A ``label`` is only valid
        with one source. The sources vanish for good: only their files remain.

        Raises:
            ConfirmationRequired: ``confirm`` is false. Nothing was changed.
            Validation: No source, too many, one twice or the root itself, a label that is too
                long or comes with several sources, or a source that has versions of its own.
            NotFound: A document does not exist or the user may not see it.
            PermissionDenied: The user may not change and delete the documents (own them).
        """
        text = self._checked(root_id, source_ids, label)
        root = await self._reader.get_document(root_id)
        sources = [await self._reader.get_document(number) for number in source_ids]
        for source in sources:
            if len(source.versions) > 1:
                count = len(source.versions)
                message = f"Document {source.id} has {count} versions and cannot be merged."
                raise Validation(
                    message,
                    hint=(
                        "Only a document with a single version can be merged. Merge into that "
                        "document instead, or delete its other versions first."
                    ),
                )
        if not confirm:
            raise ConfirmationRequired.for_effect(self._effect(root, sources, text))
        await self._writer.merge_as_versions(root_id, tuple(source_ids), text)
        merged = await self._reader.get_document(root_id)
        return VersionsMerged(
            document_id=root_id,
            merged_document_ids=tuple(source_ids),
            versions=merged.versions,
            web_url=document_link(self._web_url, root_id),
        )

    @staticmethod
    def _checked(root_id: int, source_ids: Sequence[int], label: str | None) -> str | None:
        """The cleaned label, after the checks that need no read of Paperless."""
        if not source_ids:
            message = f"Name at least one document to merge into document {root_id}."
            raise Validation(message, hint="Pass the ids of the documents in source_document_ids.")
        if len(source_ids) > MAX_SOURCES:
            message = f"At most {MAX_SOURCES} documents are merged at once; got {len(source_ids)}."
            raise Validation(message, hint="Merge in several steps.")
        if root_id in source_ids:
            message = f"Document {root_id} cannot be merged into itself."
            raise Validation(message, hint="List only the other documents as sources.")
        duplicate = next((n for n in source_ids if source_ids.count(n) > 1), None)
        if duplicate is not None:
            message = f"Document {duplicate} is listed twice."
            raise Validation(message, hint="List each document once.")
        text = clean_label(label)
        if text is not None and len(source_ids) > 1:
            message = "A label fits only when one document is merged."
            hint = "Merge one document to label it, or use set_document_version_label afterwards."
            raise Validation(message, hint=hint)
        return text

    @staticmethod
    def _effect(root: DocumentRecord, sources: Sequence[DocumentRecord], label: str | None) -> str:
        names = [f"document {s.id} {quoted(s.title)}" for s in sources]
        many = len(sources) > 1
        shown = f"{', '.join(names[:-1])} and {names[-1]}" if many else names[0]
        versions = "as its newest versions" if many else "as its newest version"
        if label:
            versions += f", labelled {quoted(label)}"
        who = "the merged documents vanish" if many else "the merged document vanishes"
        loss = "Their titles" if many else "Its title"
        return (
            f"merge {shown} into document {root.id} {quoted(root.title)} {versions}: {who} for "
            f"good, not even into the trash. {loss}, tags, correspondents, notes and other "
            f"metadata are lost; only {'their files stay' if many else 'its file stays'}, as "
            f"{'versions' if many else 'a version'} of document {root.id}"
        )
