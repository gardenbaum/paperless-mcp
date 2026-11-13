"""PDF operations: rotate, merge, split, delete pages, edit, remove a password, reprocess.

Paperless runs these as background tasks and answers "OK" without naming them; the adapter finds
the tasks, and this service reports them like an upload does (``task_outcome``), with an optional
bounded wait. Everything Paperless would refuse with a 400 and this server can see beforehand is
refused first: page ranges against the page count, the file type, the numbering of output files.

What destroys something goes through the confirmation flow (decision-6, decision-15): deleting
originals (they move to the Paperless trash), a new version that drops pages from the file the
document shows (the earlier version stays, but the document no longer shows those pages), and
reprocessing (the archived file and the text are replaced). Making new documents, and adding a
version that keeps every page (rotating, unlocking), changes nothing that existed.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from paperless_mcp.domain.documents import UploadState  # noqa: TC001 -- used at runtime in a constant
from paperless_mcp.domain.errors import ConfirmationRequired, Validation, quoted
from paperless_mcp.domain.pdf import PageSlot, PdfResult, PdfTask
from paperless_mcp.services.documents import document_link
from paperless_mcp.services.page_ranges import describe, parse_pages
from paperless_mcp.services.task_outcome import follow
from paperless_mcp.services.tasks import MAX_WAIT_SECONDS
from paperless_mcp.services.versions import require_change

if TYPE_CHECKING:
    from collections.abc import Sequence

    from paperless_mcp.domain.documents import DocumentRecord
    from paperless_mcp.domain.pdf import Output, StartedTasks
    from paperless_mcp.domain.ports import DocumentReader, PdfWriter
    from paperless_mcp.domain.tasks import Wait
    from paperless_mcp.services.tasks import TaskService

# Most documents one call works on: each is read first, so the call stays small.
MAX_DOCUMENTS: Final = 20
# Most parts a split makes, and most pages an edit lists.
MAX_PARTS: Final = 20
MAX_PAGES: Final = 500
PDF: Final = "application/pdf"
TRASH: Final = "move the original to the Paperless trash"
FINAL: Final = frozenset({"success", "failure", "revoked"})
FORBIDDEN: Final = (
    "Paperless accepted the request, but the Paperless user may not view tasks, so none can be "
    "followed."
)
UNREADABLE: Final = "Paperless accepted the request; its tasks could not be read."
NONE_FOUND: Final = "Paperless accepted the request, but no task showed up for it{reason}."
SOME_FOUND: Final = (
    "Paperless accepted the request, but only {found} of the {expected} tasks it starts showed "
    "up: the state covers {those} only."
)
UNLOCKED_NONE: Final = " (the file may not be password protected)"
REPROCESS_NOTE: Final = (
    "Paperless does not say which document a reprocess task belongs to (its tasks name none): "
    "they are the newest reprocess tasks this request started, so they cannot be tied to the "
    "documents."
)
SERIAL: Final = "archive serial number, if it has one, moves to the new document"


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _ids(numbers: Sequence[int]) -> str:
    """``3 and 5`` or ``3, 5 and 8``: two or more numbers."""
    names = [str(n) for n in numbers]
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _named(documents: Sequence[DocumentRecord]) -> str:
    """``document 5 'Title'`` for each, joined like ``_ids``."""
    names = [f"document {d.id} {quoted(d.title)}" for d in documents]
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


_WORST_FIRST: Final[tuple[UploadState, ...]] = (
    "failure",
    "revoked",
    "unknown",
    "started",
    "pending",
)


def _aggregate(states: Sequence[UploadState]) -> UploadState:
    """The state of several tasks together: the worst one counts, a running one beats done."""
    for state in _WORST_FIRST:
        if state in states:
            return state
    return "success"


@dataclass(frozen=True, slots=True)
class _Plan:
    """How a page-based request reads in a result and a refusal.

    Attributes:
        operation: The tool's operation name in the result.
        summary: What was asked, for the result.
        what: The same with the document named, for the refusal.
        pages: How many pages the source file has.
        single: Whether the result is one file, which then takes over the original's serial number.
        verb: What dropping pages is called here: drop, or delete for delete_pages.
    """

    operation: str
    summary: str
    what: str
    pages: int
    single: bool = False
    verb: str = "drop"


class PdfService:
    """Runs PDF operations for the current user and reports their tasks."""

    def __init__(
        self, reader: DocumentReader, writer: PdfWriter, tasks: TaskService, *, web_url: str
    ) -> None:
        """Read through ``reader``, write through ``writer``, follow tasks through ``tasks``.

        ``web_url`` is the Paperless web UI, for the links of a result.
        """
        self._web_url: str = web_url
        self._reader: DocumentReader = reader
        self._writer: PdfWriter = writer
        self._tasks: TaskService = tasks

    async def rotate(
        self, document_ids: Sequence[int], degrees: int, *, wait_seconds: float = 0
    ) -> PdfResult:
        """Rotate every page of the documents clockwise; each gets a new version.

        ``degrees`` is a multiple of 90 other than 0 (a negative one turns back).

        Raises:
            Validation: No document, too many, one twice or not a PDF, or a rotation that is not
                a quarter, half or three-quarter turn.
            NotFound: A document does not exist or the user may not see it.
            PermissionDenied: The user may not change a document.
        """
        turn = degrees % 360
        if degrees % 90 or not turn:
            message = f"{degrees} degrees is not a quarter turn."
            raise Validation(message, hint="Use 90, 180 or 270 (clockwise), or -90.")
        documents = await self._documents(document_ids, pdf=True)
        started = await self._writer.rotate_documents(document_ids, turn)
        summary = f"rotate {_plural(len(documents), 'document')} by {turn} degrees clockwise"
        return await self._result(
            "rotate", summary, document_ids, started, wait_seconds=wait_seconds, version=True
        )

    async def merge(
        self,
        document_ids: Sequence[int],
        metadata_document_id: int | None,
        *,
        delete_originals: bool = False,
        confirm: bool = False,
        wait_seconds: float = 0,
    ) -> PdfResult:
        """Merge the PDFs, in this order, into one new document.

        The new document takes the metadata of ``metadata_document_id``, one of the documents.
        Deleting the originals moves them to the trash and needs ``confirm``.

        Raises:
            ConfirmationRequired: ``delete_originals`` without ``confirm``. Nothing was changed.
            Validation: Fewer than two documents, a repeat, a non-PDF, or a metadata document that
                is not among them.
            NotFound: A document does not exist or the user may not see it.
            PermissionDenied: The user may not change a document.
        """
        if len(document_ids) < 2:  # noqa: PLR2004 -- merging one document is no merge
            message = "Merging needs at least two documents."
            raise Validation(message, hint="Name the documents to merge, in order.")
        if metadata_document_id is not None and metadata_document_id not in document_ids:
            message = f"Document {metadata_document_id} is not among the documents to merge."
            raise Validation(message, hint="Take the metadata from one of the merged documents.")
        documents = await self._documents(document_ids, pdf=True)
        summary = f"merge documents {_ids(document_ids)} into one new document"
        if delete_originals:
            if not confirm:
                effect = (
                    f"merge {_named(documents)} into one new document, in this order, and "
                    "move the originals to the Paperless trash (they can be restored from there); "
                    f"an original's {SERIAL}"
                )
                raise ConfirmationRequired.for_effect(effect)
            summary += " and move the originals to the Paperless trash"
        started = await self._writer.merge_documents(
            document_ids,
            metadata_document_id=metadata_document_id,
            delete_originals=delete_originals,
        )
        return await self._result(
            "merge", summary, document_ids, started, wait_seconds=wait_seconds
        )

    async def split(
        self,
        document_id: int,
        parts: Sequence[str],
        *,
        output: Output = "new_document",
        confirm: bool = False,
        wait_seconds: float = 0,
    ) -> PdfResult:
        """Split a PDF into one new document per part, each part a page range like ``1-3,5``.

        Pages in no part are in no new document. ``replace_original`` moves the original to the
        trash and needs ``confirm``.

        Raises:
            ConfirmationRequired: ``replace_original`` without ``confirm``. Nothing was changed.
            Validation: Fewer than two parts or too many, a bad or overlapping range, a page past
                the end, a non-PDF, or ``output`` ``new_version``.
            NotFound: No such document.
            PermissionDenied: The user may not change the document.
        """
        if output == "new_version":
            message = "A split makes several files, which cannot all be a version of the document."
            raise Validation(message, hint="Use new_document or replace_original.")
        if not 2 <= len(parts) <= MAX_PARTS:  # noqa: PLR2004 -- two parts make a split
            message = f"A split needs at least two parts and at most {MAX_PARTS} parts."
            raise Validation(message, hint="Give a page range per new document, e.g. '1-3'.")
        document, count = await self._paged(document_id)
        slots: list[PageSlot] = []
        seen: dict[int, int] = {}
        ranges: list[str] = []
        for number, spec in enumerate(parts):
            pages = parse_pages(spec, page_count=count)
            for page in pages:
                if page in seen:
                    message = f"Page {page} is in part {seen[page] + 1} and part {number + 1}."
                    raise Validation(message, hint="Give each page to one part.")
                seen[page] = number
            ranges.append(describe(pages))
            slots.extend(PageSlot(page=page, document=number) for page in pages)
        summary = f"split document {document_id} into {len(parts)} new documents: "
        summary += " | ".join(ranges)
        what = f"split {_named([document])} into {len(parts)} new documents ({' | '.join(ranges)})"
        plan = _Plan("split", summary, what, count)
        return await self._send_pages(
            document, slots, plan, output=output, confirm=confirm, wait_seconds=wait_seconds
        )

    async def delete_pages(
        self,
        document_id: int,
        pages: str,
        *,
        output: Output = "new_version",
        confirm: bool = False,
        wait_seconds: float = 0,
    ) -> PdfResult:
        """Make a file without the given pages (a range like ``2-3,5``), in order.

        As a new version (the default) the document shows the shorter file and the earlier version
        stays; ``confirm`` is needed, as for ``replace_original`` (new document, the original goes
        to the trash). Into a ``new_document`` nothing existing changes.

        Raises:
            ConfirmationRequired: The output changes the document and ``confirm`` is false.
            Validation: A bad range, a page past the end, every page deleted, or a non-PDF.
            NotFound: No such document.
            PermissionDenied: The user may not change the document.
        """
        document, count = await self._paged(document_id)
        deleted = parse_pages(pages, page_count=count)
        kept = [page for page in range(1, count + 1) if page not in deleted]
        if not kept:
            message = "Deleting every page leaves nothing."
            raise Validation(message, hint="Delete the whole document with delete_document.")
        where = "as a new version" if output == "new_version" else "into a new document"
        plan = _Plan(
            "delete_pages",
            f"delete {describe(deleted)} of document {document_id} {where}",
            f"delete {describe(deleted)} of {_named([document])} {where}",
            count,
            single=True,
            verb="delete",
        )
        slots = [PageSlot(page=page) for page in kept]
        return await self._send_pages(
            document, slots, plan, output=output, confirm=confirm, wait_seconds=wait_seconds
        )

    async def edit(
        self,
        document_id: int,
        pages: Sequence[PageSlot],
        *,
        output: Output = "new_document",
        confirm: bool = False,
        wait_seconds: float = 0,
    ) -> PdfResult:
        """Build files from the pages of one PDF: pick, order, turn and group them.

        Raises:
            ConfirmationRequired: The output drops pages from what the document shows, or trashes
                the original, and ``confirm`` is false. Nothing was changed.
            Validation: No page or too many, a page that does not exist, a rotation that is not a
                multiple of 90, output files not numbered from 0 without gaps, several files as
                a new version, or a non-PDF.
            NotFound: No such document.
            PermissionDenied: The user may not change the document.
        """
        if not pages or len(pages) > MAX_PAGES:
            message = f"Name at least one page and at most {MAX_PAGES} pages."
            raise Validation(message, hint="Each page of the result is one entry.")
        document, count = await self._paged(document_id)
        for slot in pages:
            if slot.rotate % 90:
                message = f"A rotation must be a multiple of 90 degrees, not {slot.rotate}."
                raise Validation(message, hint="Use 0, 90, 180 or 270.")
            _ = parse_pages(str(slot.page), page_count=count)
        files = sorted({slot.document for slot in pages})
        if files[0] < 0:
            message = "Output files are numbered from 0."
            raise Validation(message, hint="Use 0 for the first file, 1 for the second.")
        if files != list(range(len(files))):
            message = "Output files must be numbered 0, 1, 2 and so on without gaps."
            raise Validation(message, hint="Renumber the files so that none is skipped.")
        if output == "new_version" and len(files) > 1:
            message = "A new version is a single file, but the pages go to several files."
            raise Validation(message, hint="Use new_document or replace_original.")
        turned = sum(1 for slot in pages if slot.rotate % 360)
        shape = f"{_plural(len(pages), 'page')}, {turned} turned"
        where = (
            "as a new version"
            if output == "new_version"
            else f"into {_plural(len(files), 'new document')}"
        )
        summary = f"edit document {document_id} {where} ({shape})"
        what = f"edit {_named([document])} {where} ({shape})"
        plan = _Plan("edit_pdf", summary, what, count, single=len(files) == 1)
        return await self._send_pages(
            document, list(pages), plan, output=output, confirm=confirm, wait_seconds=wait_seconds
        )

    async def remove_password(
        self,
        document_id: int,
        password: str,
        *,
        output: Output = "new_document",
        confirm: bool = False,
        wait_seconds: float = 0,
    ) -> PdfResult:
        """Unlock a password protected PDF; the result goes to ``output``.

        ``replace_original`` moves the locked file to the trash and needs ``confirm``. A new
        version keeps the locked one as an older version. A file without a password starts no task.

        Raises:
            ConfirmationRequired: ``replace_original`` without ``confirm``. Nothing was changed.
            Validation: An empty or wrong password, or a non-PDF.
            NotFound: No such document.
            PermissionDenied: The user may not change the document.
        """
        if not password:
            message = "The password is empty."
            raise Validation(message, hint="Give the password that opens the file.")
        [document] = await self._documents([document_id], pdf=True)
        where = {
            "new_document": "into a new document",
            "new_version": "as a new version",
            "replace_original": "into a new document and move the original to the Paperless trash",
        }[output]
        summary = f"remove the password of document {document_id} {where}"
        if output == "replace_original" and not confirm:
            what = f"remove the password of {_named([document])} {where}; the original's {SERIAL}"
            raise ConfirmationRequired.for_effect(what)
        started = await self._writer.remove_password(document_id, password, output=output)
        return await self._result(
            "remove_password",
            summary,
            [document_id],
            started,
            wait_seconds=wait_seconds,
            version=output == "new_version",
            no_task=UNLOCKED_NONE,
        )

    async def reprocess(
        self,
        document_ids: Sequence[int],
        *,
        remote_ocr: bool = False,
        confirm: bool = False,
        wait_seconds: float = 0,
    ) -> PdfResult:
        """Run OCR and archiving again; the archived file and the text are replaced.

        Raises:
            ConfirmationRequired: ``confirm`` is false. Nothing was changed.
            Validation: No document, too many, or one twice.
            NotFound: A document does not exist or the user may not see it.
            PermissionDenied: The user may not change a document.
        """
        documents = await self._documents(document_ids, pdf=False)
        if not confirm:
            effect = (
                f"reprocess {_named(documents)} (OCR and archiving run again; the archived PDF "
                "and the extracted text are replaced, so manual changes to the text are lost)"
            )
            raise ConfirmationRequired.for_effect(effect)
        started = await self._writer.reprocess_documents(document_ids, remote_ocr=remote_ocr)
        engine = "remote OCR" if remote_ocr else "local OCR"
        summary = f"reprocess {_plural(len(documents), 'document')} ({engine})"
        return await self._result(
            "reprocess",
            summary,
            document_ids,
            started,
            wait_seconds=wait_seconds,
            note=REPROCESS_NOTE,
        )

    async def _documents(self, document_ids: Sequence[int], *, pdf: bool) -> list[DocumentRecord]:
        """The documents, after the checks that need no write; each must be ours to change."""
        if not document_ids:
            message = "Name at least one document."
            raise Validation(message, hint="Pass the document ids from search_documents.")
        if len(document_ids) > MAX_DOCUMENTS:
            message = f"Work on at most {MAX_DOCUMENTS} documents at once; got {len(document_ids)}."
            raise Validation(message, hint="Work in several steps.")
        repeated = next((n for n in document_ids if document_ids.count(n) > 1), None)
        if repeated is not None:
            message = f"Document {repeated} is listed twice."
            raise Validation(message, hint="List each document once.")
        found: list[DocumentRecord] = []
        for number in document_ids:
            document = await self._reader.get_document(number)
            require_change(document)
            if pdf and document.mime_type != PDF:
                message = f"Document {number} is not a PDF ({document.mime_type})."
                raise Validation(message, hint="Only PDF files can be changed this way.")
            found.append(document)
        return found

    async def _paged(self, document_id: int) -> tuple[DocumentRecord, int]:
        """The PDF whose pages are named, and its page count, which must be known."""
        [document] = await self._documents([document_id], pdf=True)
        count = document.page_count
        if count is None:
            message = f"The page count of document {document_id} is unknown."
            raise Validation(
                message,
                hint=(
                    "Encrypted PDFs have none: remove the password first with "
                    "remove_document_password."
                ),
            )
        return document, count

    async def _send_pages(  # noqa: PLR0913 -- the pieces of one page-based request
        self,
        document: DocumentRecord,
        slots: list[PageSlot],
        plan: _Plan,
        *,
        output: Output,
        confirm: bool,
        wait_seconds: float,
    ) -> PdfResult:
        """Confirm what needs it, then edit: the common end of split, delete_pages and edit."""
        used = {slot.page for slot in slots}
        dropped = [page for page in range(1, plan.pages + 1) if page not in used]
        trashes = output == "replace_original"
        drops = output == "new_version" and bool(dropped)
        if (trashes or drops) and not confirm:
            if trashes:
                effect = f"{plan.what} and {TRASH}"
                if plan.single:
                    effect += f"; the original's {SERIAL}"
            else:
                effect = (
                    f"{plan.verb} {describe(dropped)} of {_named([document])}: the document then "
                    "shows a new version without them, and the earlier version with all pages "
                    "stays until that version is deleted"
                )
            raise ConfirmationRequired.for_effect(effect)
        started = await self._writer.edit_pdf(document.id, slots, output=output)
        summary = f"{plan.summary} and {TRASH}" if trashes else plan.summary
        return await self._result(
            plan.operation,
            summary,
            [document.id],
            started,
            wait_seconds=wait_seconds,
            version=output == "new_version",
        )

    async def _result(  # noqa: PLR0913 -- the pieces of one result
        self,
        operation: str,
        summary: str,
        sources: Sequence[int],
        started: StartedTasks,
        *,
        wait_seconds: float,
        version: bool = False,
        no_task: str = "",
        note: str = "",
    ) -> PdfResult:
        """Follow the tasks for up to ``wait_seconds`` altogether and report them.

        ``version``: the result is a new version of the source (the produced ids are version ids,
        so the links are the sources'). ``no_task`` explains why a request may have no task,
        ``note`` is added to the message.
        """
        bound = min(max(wait_seconds, 0), MAX_WAIT_SECONDS)
        tasks: list[PdfTask] = []
        waited = 0.0
        for task_id in started.ids:
            outcome = await follow(
                self._tasks, task_id, max(bound - waited, 0), accepted="the request"
            )
            waited += outcome.waited_seconds
            tasks.append(
                PdfTask(
                    task_id=task_id,
                    state=outcome.state,
                    message=outcome.message,
                    document_ids=outcome.document_ids,
                )
            )
        states: list[UploadState] = [task.state for task in tasks]
        complete = started.problem is None and len(tasks) == started.expected
        notes = [task.message for task in tasks if task.message][:1]
        if started.problem == "forbidden":
            notes.append(FORBIDDEN)
        elif started.problem == "failed":
            notes.append(UNREADABLE)
        elif not tasks:
            notes.append(NONE_FOUND.format(reason=no_task))
        elif not complete:
            those = "that one" if len(tasks) == 1 else "those"
            found = len(tasks)
            notes.append(SOME_FOUND.format(found=found, expected=started.expected, those=those))
        if note:
            notes.append(note)
        wait: Wait = "not_requested"
        if tasks and bound > 0:
            wait = "finished" if all(s in FINAL for s in states) else "timed_out"
        produced = tuple(n for task in tasks for n in task.document_ids)
        links = tuple(document_link(self._web_url, n) for n in sources)
        return PdfResult(
            operation=operation,
            summary=summary,
            source_document_ids=tuple(sources),
            source_links=links,
            state=_aggregate(states) if complete else "unknown",
            message=" ".join(notes) or None,
            tasks=tuple(tasks),
            produced_ids=produced,
            produced_links=self._produced_links(sources, produced, version=version),
            wait=wait,
            waited_seconds=round(waited, 1),
        )

    def _produced_links(
        self, sources: Sequence[int], produced: Sequence[int], *, version: bool
    ) -> tuple[str, ...]:
        """Links to what was made; a new version is made on its source, which gets the link."""
        if not produced:
            return ()
        numbers = sources if version else produced
        return tuple(document_link(self._web_url, n) for n in numbers[: len(produced)])
