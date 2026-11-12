"""Notes on documents: adding, and deleting behind the confirmation flow (decision-6).

Paperless accepts empty notes and keeps them, so the text is checked here. A deletion reads the
note first so that the refusal without ``confirm`` can say which note and document are meant,
and so that an unknown note is reported before anything else.
"""

from typing import TYPE_CHECKING

from paperless_mcp.domain.documents import NoteAdded, NoteDeleted
from paperless_mcp.domain.errors import ConfirmationRequired, NotFound, Validation, quoted
from paperless_mcp.services.documents import document_link

if TYPE_CHECKING:
    from paperless_mcp.domain.ports import DocumentReader, DocumentWriter

# Most characters of one note: Paperless sets no limit, but a note is a remark, not a document.
MAX_NOTE_CHARS = 10_000
# Characters of a note's start shown when asking for confirmation to delete it.
EXCERPT_CHARS = 40


class NoteService:
    """Adds and deletes notes for the current user."""

    def __init__(self, reader: DocumentReader, writer: DocumentWriter, *, web_url: str) -> None:
        """Read through ``reader``, write through ``writer``; ``web_url`` is the Paperless UI."""
        self._reader: DocumentReader = reader
        self._writer: DocumentWriter = writer
        self._web_url: str = web_url

    async def add(self, document_id: int, text: str) -> NoteAdded:
        """Add a note with ``text`` to a document.

        Raises:
            Validation: The text is empty or blank, or longer than ``MAX_NOTE_CHARS``.
            NotFound: No such document.
            PermissionDenied: The user may not add notes, or not to this document.
        """
        if not text.strip():
            message = "A note cannot be empty."
            raise Validation(message, hint="Pass the text of the note.")
        if len(text) > MAX_NOTE_CHARS:
            message = f"A note holds at most {MAX_NOTE_CHARS} characters; this one has {len(text)}."
            raise Validation(message, hint="Shorten the note or split it into several.")
        note = await self._writer.add_note(document_id, text)
        return NoteAdded(
            document_id=document_id, note=note, web_url=document_link(self._web_url, document_id)
        )

    async def delete(self, document_id: int, note_id: int, *, confirm: bool = False) -> NoteDeleted:
        """Delete a note for good; without ``confirm`` nothing changes and the error says why.

        Reads the document and its notes first, which needs the permission to view notes.

        Raises:
            ConfirmationRequired: ``confirm`` is false. Nothing was changed.
            NotFound: No such document, or no such note on it.
            PermissionDenied: The user may not view or delete notes, or not on this document.
        """
        document = await self._reader.get_document(document_id)
        notes = await self._reader.list_document_notes(document_id)
        found = [n for n in notes if n.id == note_id]
        if not found:
            message = f"Document {document_id} has no note {note_id}."
            raise NotFound(message, hint="List the notes with get_document and include notes.")
        if not confirm:
            note = found[0]
            who = f"by {note.user}, " if note.user else ""
            effect = (
                f"delete note {note_id} ({who}{len(note.note)} characters, starting "
                f"{quoted(note.note, EXCERPT_CHARS)}) from document {document_id} "
                f"{quoted(document.title)} for good; notes have no trash"
            )
            raise ConfirmationRequired.for_effect(effect)
        await self._writer.delete_note(document_id, note_id)
        return NoteDeleted(
            document_id=document_id,
            note_id=note_id,
            web_url=document_link(self._web_url, document_id),
        )
