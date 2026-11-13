"""The Paperless trash: listing it, restoring from it and emptying it behind confirm.

Restoring is the safe direction and takes no confirmation. Emptying removes documents and
deleted versions for good (decision-6, decision-15), so it never defaults to anything: it needs
ids or ``all=true``, never both, and ``confirm`` in both cases. The refusal names what would be
destroyed. Both actions look the ids up in the trash first, so a caller is never asked to confirm
what cannot happen, and Paperless is only ever sent explicit ids: a request without ids means
"everything" to it.
"""

from typing import TYPE_CHECKING, Final

from paperless_mcp.domain.errors import ConfirmationRequired, NotFound, Validation, quoted
from paperless_mcp.domain.trash import TrashedDocument, TrashEmptied, TrashList, TrashRestored
from paperless_mcp.services.document_edits import COUNTED

if TYPE_CHECKING:
    from collections.abc import Sequence

    from paperless_mcp.domain.ports import TrashReader, TrashWriter
    from paperless_mcp.services.taxonomy import TaxonomyResolver

# Most entries one call names: bounded, so the confirmation text and the lookups stay small.
MAX_IDS: Final = 100
# Entries a confirmation names one by one; the rest are counted.
SHOWN: Final = 10
# Most entries ``all=true`` handles at once (it names every id to Paperless).
MAX_ALL: Final = 1000
# Entries per request while the trash is read through.
SCAN_SIZE: Final = 100

LOST = (
    "They cannot be restored afterwards: the files, text, notes and metadata of documents, and "
    "the deleted versions, are lost for good"
)


def _counted(count: int, noun: str) -> str:
    """``1 document``, ``2 documents``, ``1 entry``, ``2 entries``."""
    if count == 1:
        return f"1 {noun}"
    return f"{count} {noun.removesuffix('y')}ies" if noun.endswith("y") else f"{count} {noun}s"


def _described(entry: TrashedDocument, *, deleted: bool = True) -> str:
    """One entry in words: its id and title, what it is, and optionally when it was deleted."""
    when = f" (deleted {entry.deleted_at.date().isoformat()})" if deleted else ""
    if entry.version_of is None:
        return f"document {entry.id} {quoted(entry.title)}{when}"
    return f"version {entry.id} of document {entry.version_of} {quoted(entry.title)}{when}"


def _joined(parts: Sequence[str]) -> str:
    return parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"


class TrashService:
    """Lists, restores and empties the Paperless trash for the current user."""

    def __init__(
        self,
        reader: TrashReader,
        writer: TrashWriter,
        taxonomy: TaxonomyResolver,
        *,
        max_all: int = MAX_ALL,
    ) -> None:
        """Read through ``reader``, change through ``writer``.

        ``taxonomy`` forgets the document counts after a restore. ``max_all`` is the most entries
        ``all=true`` empties at once.
        """
        self._reader: TrashReader = reader
        self._writer: TrashWriter = writer
        self._taxonomy: TaxonomyResolver = taxonomy
        self._max_all: int = max_all

    async def list(self, *, page: int, page_size: int) -> TrashList:
        """One page of the trash.

        Raises:
            NotFound: ``page`` is beyond the last page.
            PermissionDenied: The user may not view documents.
        """
        found = await self._reader.list_trash(page=page, page_size=page_size)
        return TrashList(
            count=found.count, page=page, next_page=found.next_page, entries=found.entries
        )

    async def restore(self, document_ids: Sequence[int]) -> TrashRestored:
        """Take the entries out of the trash: documents come back, versions are re-attached.

        Raises:
            Validation: No id, or more than ``MAX_IDS``.
            NotFound: An id is not in the trash (or not one the user may see). Nothing is
                restored.
            PermissionDenied: The user may not delete documents, or one of these.
        """
        ids = self._distinct(document_ids)
        entries = await self._entries(ids)
        await self._writer.restore_from_trash(ids)
        for kind in COUNTED:
            self._taxonomy.invalidate(kind)
        documents = [e for e in entries if e.version_of is None]
        versions = [e for e in entries if e.version_of is not None]
        kinds = ((documents, "document"), (versions, "version"))
        counts = [_counted(len(found), noun) for found, noun in kinds if found]
        parts = [
            _described(e, deleted=False)
            + (" (attached to the document again)" if e.version_of else "")
            for e in entries
        ]
        message = f"Restored {' and '.join(counts)} from the trash: {_joined(parts)}."
        return TrashRestored(restored=tuple(entries), message=message)

    async def empty(
        self, document_ids: Sequence[int] | None, *, everything: bool, confirm: bool = False
    ) -> TrashEmptied:
        """Remove entries from the trash for good: the named ones, or every one with ``everything``.

        Without ``confirm`` nothing changes and the refusal names what would be destroyed.

        Raises:
            ConfirmationRequired: ``confirm`` is false. Nothing was changed.
            Validation: Neither ids nor ``everything``, or both; too many ids; ``everything`` on
                a trash larger than the limit.
            NotFound: An id is not in the trash (or not one the user may see).
            PermissionDenied: The user may not delete documents, or one of these.
        """
        if document_ids and everything:
            message = "Pass either document_ids or all=true, not both."
            raise Validation(
                message, hint="Use document_ids for some entries, all=true for every one."
            )
        if not document_ids and not everything:
            message = "Name what to empty: nothing was emptied."
            hint = "Pass document_ids from list_trash, or all=true to empty the whole trash."
            raise Validation(message, hint=hint)
        if document_ids:
            ids = self._distinct(document_ids)
            entries = await self._entries(ids)
        else:
            entries = await self._everything()
            if not entries:
                return TrashEmptied(
                    document_ids=(), count=0, message="The trash is empty: nothing was deleted."
                )
        if not confirm:
            raise ConfirmationRequired.for_effect(self._effect(entries, everything=everything))
        await self._writer.empty_trash([e.id for e in entries])
        deleted = tuple(e.id for e in entries)
        if everything:
            message = f"Permanently deleted ALL {len(entries)} entries of the trash."
        else:
            message = (
                f"Permanently deleted {_counted(len(entries), 'entry')} from the trash: "
                f"{_joined([_described(e, deleted=False) for e in entries])}."
            )
        return TrashEmptied(document_ids=deleted, count=len(entries), message=message)

    @staticmethod
    def _distinct(document_ids: Sequence[int]) -> list[int]:
        """The ids once each, in the order given.

        Raises:
            Validation: No id, or more than ``MAX_IDS``.
        """
        ids = list(dict.fromkeys(document_ids))
        if not ids:
            message = "Name at least one trash entry."
            raise Validation(message, hint="Pass the ids from list_trash.")
        if len(ids) > MAX_IDS:
            message = f"Name at most {MAX_IDS} trash entries at once; got {len(ids)}."
            raise Validation(message, hint="Work in several steps.")
        return ids

    async def _entries(self, ids: Sequence[int]) -> list[TrashedDocument]:
        """The entries with these ids, in that order, reading the trash until all are found.

        Raises:
            NotFound: Some ids are not in the trash, or not visible to the user.
        """
        wanted = set(ids)
        found: dict[int, TrashedDocument] = {}
        page: int | None = 1
        while page is not None and wanted - found.keys():
            listed = await self._reader.list_trash(page=page, page_size=SCAN_SIZE)
            found |= {e.id: e for e in listed.entries if e.id in wanted}
            page = listed.next_page
        missing = [number for number in ids if number not in found]
        if missing:
            message = f"Not in the trash: {', '.join(map(str, missing))}."
            hint = (
                "List the trash with list_trash. Entries of documents the user does not own are "
                "not listed, and a version is listed only while its document is not in the trash."
            )
            raise NotFound(message, hint=hint)
        return [found[number] for number in ids]

    async def _everything(self) -> list[TrashedDocument]:
        """Every entry the user sees, over all pages.

        Raises:
            Validation: The trash holds more than ``max_all`` entries.
        """
        entries: list[TrashedDocument] = []
        page: int | None = 1
        while page is not None:
            listed = await self._reader.list_trash(page=page, page_size=SCAN_SIZE)
            if listed.count > self._max_all:
                message = (
                    f"The trash holds {listed.count} entries; all=true handles at most "
                    f"{self._max_all} at once."
                )
                raise Validation(message, hint="Empty in steps with document_ids from list_trash.")
            entries.extend(listed.entries)
            page = listed.next_page
        return entries

    @staticmethod
    def _effect(entries: Sequence[TrashedDocument], *, everything: bool) -> str:
        """What confirming would do, naming the first ``SHOWN`` entries and counting the rest."""
        shown = [_described(e) for e in entries[:SHOWN]]
        if len(entries) > SHOWN:
            shown.append(f"{len(entries) - SHOWN} more")
        if everything:
            head = (
                f"permanently delete ALL {len(entries)} entries of the Paperless trash (every "
                "entry the user can see)"
            )
        else:
            head = f"permanently delete {_counted(len(entries), 'entry')} from the Paperless trash"
        return f"{head}: {_joined(shown)}. {LOST}"
