"""PdfWriter on the PDF endpoints of Paperless 3.x (API version 10).

``POST /api/documents/{rotate,merge,edit_pdf,remove_password,reprocess}/`` answer
``{"result": "OK"}`` once the work is queued, and name no task (probed on 3.4.2). The work runs
as tasks that appear in ``GET /api/tasks/`` within milliseconds:

- ``consume_file`` per produced file, named by ``input_data.filename``: ``{id}_rotated.pdf``,
  ``{id}_edit_{n}.pdf`` (new documents) or ``{id}_edited.pdf`` (a new version),
  ``{id}_{id}_merged.pdf``, ``{id}_unprotected.pdf``;
- ``reprocess_document`` per document;
- a trailing ``bulk_delete`` when originals are deleted (not reported).

So the writer notes the newest task before the request and afterwards looks for newer tasks of
the operation's kind, for a short bounded time. A user who may not view tasks gets none back.
"""

import asyncio
import re
import time
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, JsonValue

from paperless_mcp.domain.errors import PermissionDenied, Validation
from paperless_mcp.paperless.errors import REJECTED
from paperless_mcp.paperless.pagination import Page

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from paperless_mcp.domain.pdf import Output, PageSlot
    from paperless_mcp.paperless.client import PaperlessClient

BASE = "/api/documents/"
TASKS = "/api/tasks/"
# How long the tasks of a request may take to show up, and how often the list is read meanwhile.
SETTLE_SECONDS: Final = 3.0
POLL_SECONDS: Final = 0.1
# Most tasks one read of the list looks at (an operation starts at most MAX_DOCUMENTS of them).
LOOKBACK: Final = 100

REFUSED = "The Paperless user may not change these documents."
REFUSED_HINT = (
    "Changing a PDF needs the permission to change documents and a change grant on (or "
    "ownership of) every document; a new document needs the permission to add documents, and "
    "deleting an original also needs ownership and the permission to delete."
)


class _Result(BaseModel):
    result: str


class _Task(BaseModel):
    id: int
    task_id: str
    task_type: str
    input_data: JsonValue = None

    @property
    def filename(self) -> str:
        data = self.input_data
        name = data.get("filename") if isinstance(data, dict) else None
        return name if isinstance(name, str) else ""


type Match = Callable[[_Task], bool]


def _named(names: Sequence[str]) -> Match:
    wanted = frozenset(names)
    return lambda task: task.task_type == "consume_file" and task.filename in wanted


def _pattern(pattern: str) -> Match:
    compiled = re.compile(pattern)
    return lambda task: (
        task.task_type == "consume_file" and compiled.fullmatch(task.filename) is not None
    )


def _reprocessing(task: _Task) -> bool:
    return task.task_type == "reprocess_document"


def _merged(document_ids: Sequence[int]) -> Match:
    wanted = frozenset(str(n) for n in document_ids)

    def matches(task: _Task) -> bool:
        stem = task.filename.removesuffix("_merged.pdf")
        return (
            task.task_type == "consume_file"
            and stem != task.filename
            and frozenset(stem.split("_")) == wanted
        )

    return matches


def _refused() -> PermissionDenied:
    return PermissionDenied(REFUSED, hint=REFUSED_HINT)


def not_editable() -> Validation:
    """The error for an edit Paperless could not perform (it answers a plain-text 400)."""
    return Validation(
        "Paperless could not edit the file: it may not be a PDF.",
        hint="Only PDF files can be edited; check the document's type.",
    )


def wrong_password() -> Validation:
    """The error for a password removal Paperless could not perform (a plain-text 400)."""
    return Validation(
        "Paperless could not remove the password: it is wrong, or the file is not a PDF.",
        hint="Check the password; it is case sensitive.",
    )


def _plain(error: Validation, replacement: Validation) -> Validation:
    """``error`` unless it carries no details of Paperless's, then ``replacement`` instead."""
    return replacement if error.message == f"{REJECTED}." else error


class PaperlessPdfWriter:
    """``PdfWriter`` over the Paperless REST API."""

    def __init__(
        self,
        client: PaperlessClient,
        *,
        settle_seconds: float = SETTLE_SECONDS,
        poll_seconds: float = POLL_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Write through ``client``; the other arguments bound the lookup of the new tasks."""
        self._client: PaperlessClient = client
        self._settle: float = settle_seconds
        self._poll: float = poll_seconds
        self._sleep: Callable[[float], Awaitable[None]] = sleep
        self._clock: Callable[[], float] = clock

    async def rotate_documents(self, document_ids: Sequence[int], degrees: int) -> tuple[str, ...]:
        """See ``PdfWriter.rotate_documents``: one ``{id}_rotated.pdf`` task per document."""
        body: dict[str, JsonValue] = {"documents": list(document_ids), "degrees": degrees}
        match = _named([f"{n}_rotated.pdf" for n in document_ids])
        return await self._run("rotate", body, match, len(document_ids))

    async def merge_documents(
        self,
        document_ids: Sequence[int],
        *,
        metadata_document_id: int | None,
        delete_originals: bool,
    ) -> tuple[str, ...]:
        """See ``PdfWriter.merge_documents``: one task, the file named after the documents."""
        body: dict[str, JsonValue] = {
            "documents": list(document_ids),
            "metadata_document_id": metadata_document_id,
            "delete_originals": delete_originals,
        }
        return await self._run("merge", body, _merged(document_ids), 1)

    async def edit_pdf(
        self, document_id: int, pages: Sequence[PageSlot], *, output: Output
    ) -> tuple[str, ...]:
        """See ``PdfWriter.edit_pdf``: a task per output file.

        As a new version the file is ``{id}_edited.pdf``; as new documents ``{id}_edit_{n}.pdf``
        for every output file, which is how many files the pages go to.
        """
        body: dict[str, JsonValue] = {
            "documents": [document_id],
            "operations": [{"page": p.page, "rotate": p.rotate, "doc": p.document} for p in pages],
            "update_document": output == "new_version",
            "delete_original": output == "replace_original",
        }
        if output == "new_version":
            match, expected = _named([f"{document_id}_edited.pdf"]), 1
        else:
            match = _pattern(rf"{document_id}_edit_\d+\.pdf")
            expected = len({p.document for p in pages})
        try:
            return await self._run("edit_pdf", body, match, expected)
        except Validation as error:
            raise _plain(error, not_editable()) from None

    async def remove_password(
        self, document_id: int, password: str, *, output: Output
    ) -> tuple[str, ...]:
        """See ``PdfWriter.remove_password``: one ``{id}_unprotected.pdf`` task, if any.

        A wrong password is a 400 with a plain-text body; a file without a password is a 200
        that starts no task.
        """
        body: dict[str, JsonValue] = {
            "documents": [document_id],
            "password": password,
            "update_document": output == "new_version",
            "delete_original": output == "replace_original",
        }
        try:
            return await self._run(
                "remove_password", body, _named([f"{document_id}_unprotected.pdf"]), 1
            )
        except Validation as error:
            raise _plain(error, wrong_password()) from None

    async def reprocess_documents(
        self, document_ids: Sequence[int], *, remote_ocr: bool
    ) -> tuple[str, ...]:
        """See ``PdfWriter.reprocess_documents``: one ``reprocess_document`` task per document."""
        body: dict[str, JsonValue] = {"documents": list(document_ids), "remote_ocr": remote_ocr}
        return await self._run("reprocess", body, _reprocessing, len(document_ids))

    async def _run(
        self, endpoint: str, body: dict[str, JsonValue], match: Match, expected: int
    ) -> tuple[str, ...]:
        """Send the request, then collect the ids of the new tasks that ``match``.

        The newest task before the request is the line between old and new tasks. A refusal to
        read the list (no permission to view tasks) skips the lookup: the request is sent all
        the same.
        """
        newest = await self._newest_task()
        try:
            _ = await self._client.call("POST", f"{BASE}{endpoint}/", _Result, json=body)
        except PermissionDenied:
            raise _refused() from None
        if newest is None:
            return ()
        deadline = self._clock() + self._settle
        while True:
            found = await self._tasks_after(newest, match)
            left = deadline - self._clock()
            if len(found) >= expected or left <= 0:
                return tuple(task.task_id for task in found)
            await self._sleep(min(self._poll, left))

    async def _newest_task(self) -> int | None:
        """The database id of the newest task, 0 if there is none, None if the user may not see."""
        try:
            page = await self._client.get(
                TASKS, Page[_Task], params={"ordering": "-id", "page_size": 1}
            )
        except PermissionDenied:
            return None
        return page.results[0].id if page.results else 0

    async def _tasks_after(self, newest: int, match: Match) -> list[_Task]:
        """The tasks newer than ``newest`` that ``match``, oldest first."""
        page = await self._client.get(
            TASKS, Page[_Task], params={"ordering": "-id", "page_size": LOOKBACK}
        )
        return sorted((t for t in page.results if t.id > newest and match(t)), key=lambda t: t.id)
