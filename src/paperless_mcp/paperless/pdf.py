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

from paperless_mcp.domain.errors import PaperlessMcpError, PermissionDenied, Validation
from paperless_mcp.domain.pdf import StartedTasks
from paperless_mcp.paperless.errors import REJECTED
from paperless_mcp.paperless.pagination import Page

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from paperless_mcp.domain.pdf import LookupProblem, Output, PageSlot
    from paperless_mcp.paperless.client import PaperlessClient

BASE = "/api/documents/"
TASKS = "/api/tasks/"
SETTINGS = "/api/ui_settings/"
# How long the tasks of a request may take to show up, and how often the list is read meanwhile.
SETTLE_SECONDS: Final = 3.0
POLL_SECONDS: Final = 0.1
# Most tasks one read of the list looks at (an operation starts at most MAX_DOCUMENTS of them).
LOOKBACK: Final = 100

REFUSED = "The Paperless user may not change these documents."
REFUSED_HINT = (
    "Rotating, splitting, deleting pages, editing and removing a password need the permission to "
    "change documents and ownership of every document (or no owner): a change grant is not "
    "enough. Merging and reprocessing accept a change grant. A new document needs the permission "
    "to add documents, and deleting an original also needs ownership and the permission to delete."
)


class _Result(BaseModel):
    result: str


class _Task(BaseModel):
    id: int
    task_id: str
    task_type: str
    date_created: str
    owner: int | None = None
    input_data: JsonValue = None

    @property
    def filename(self) -> str:
        data = self.input_data
        name = data.get("filename") if isinstance(data, dict) else None
        return name if isinstance(name, str) else ""


class _User(BaseModel):
    id: int


class _Settings(BaseModel):
    user: _User


type Match = Callable[[_Task], bool]

CONSUME = "consume_file"
REPROCESS = "reprocess_document"
# Paperless names the merged file after the documents, joined and cut to this many characters.
MERGED_NAME_LIMIT: Final = 100


def _named(names: Sequence[str]) -> Match:
    wanted = frozenset(names)
    return lambda task: task.filename in wanted


def _pattern(pattern: str) -> Match:
    compiled = re.compile(pattern)
    return lambda task: compiled.fullmatch(task.filename) is not None


def _merged(document_ids: Sequence[int]) -> Match:
    joined = "_".join(str(n) for n in document_ids)
    return _named([f"{joined[:MERGED_NAME_LIMIT]}_merged.pdf"])


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
        self._lock: asyncio.Lock = asyncio.Lock()

    async def rotate_documents(self, document_ids: Sequence[int], degrees: int) -> StartedTasks:
        """See ``PdfWriter.rotate_documents``: one ``{id}_rotated.pdf`` task per document."""
        body: dict[str, JsonValue] = {"documents": list(document_ids), "degrees": degrees}
        match = _named([f"{n}_rotated.pdf" for n in document_ids])
        return await self._run("rotate", body, CONSUME, match, len(document_ids))

    async def merge_documents(
        self,
        document_ids: Sequence[int],
        *,
        metadata_document_id: int | None,
        delete_originals: bool,
    ) -> StartedTasks:
        """See ``PdfWriter.merge_documents``: one task, the file named after the documents."""
        body: dict[str, JsonValue] = {
            "documents": list(document_ids),
            "metadata_document_id": metadata_document_id,
            "delete_originals": delete_originals,
        }
        return await self._run("merge", body, CONSUME, _merged(document_ids), 1)

    async def edit_pdf(
        self, document_id: int, pages: Sequence[PageSlot], *, output: Output
    ) -> StartedTasks:
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
            return await self._run("edit_pdf", body, CONSUME, match, expected)
        except Validation as error:
            raise _plain(error, not_editable()) from None

    async def remove_password(
        self, document_id: int, password: str, *, output: Output
    ) -> StartedTasks:
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
                "remove_password", body, CONSUME, _named([f"{document_id}_unprotected.pdf"]), 1
            )
        except Validation as error:
            raise _plain(error, wrong_password()) from None

    async def reprocess_documents(
        self, document_ids: Sequence[int], *, remote_ocr: bool
    ) -> StartedTasks:
        """See ``PdfWriter.reprocess_documents``: one ``reprocess_document`` task per document."""
        body: dict[str, JsonValue] = {"documents": list(document_ids), "remote_ocr": remote_ocr}
        return await self._run("reprocess", body, REPROCESS, None, len(document_ids))

    async def _run(
        self,
        endpoint: str,
        body: dict[str, JsonValue],
        kind: str,
        match: Match | None,
        expected: int,
    ) -> StartedTasks:
        """Send the request, then collect the new tasks of ``kind`` that ``match``.

        Serialised, so that two requests cannot share a mark. The mark is the newest task of this
        user and kind before the request; new tasks are those after it. Nothing that goes wrong
        in reading the tasks is raised once the request was accepted: it is reported in the
        result, because an error would invite a repeat of work that is already queued.
        """
        async with self._lock:
            user = await self._user()
            mark, problem = await self._mark(kind, user)
            try:
                _ = await self._client.call("POST", f"{BASE}{endpoint}/", _Result, json=body)
            except PermissionDenied:
                raise _refused() from None
            if problem is not None:
                return StartedTasks(ids=(), expected=expected, problem=problem)
            found: list[_Task] = []
            deadline = self._clock() + self._settle
            while True:
                try:
                    found = await self._tasks_after(mark, kind, user, match)
                except PaperlessMcpError:
                    # Fewer than expected were found, or the loop would have ended.
                    ids = tuple(task.task_id for task in found)
                    return StartedTasks(ids=ids, expected=expected, problem="failed")
                left = deadline - self._clock()
                if len(found) >= expected or left <= 0:
                    return StartedTasks(ids=_ids(found, expected), expected=expected)
                await self._sleep(min(self._poll, left))

    async def _mark(self, kind: str, user: int | None) -> tuple[_Task | None, LookupProblem | None]:
        """The newest task of this user and kind and, if it cannot be read, why not."""
        try:
            return await self._newest(kind, user), None
        except PermissionDenied:
            return None, "forbidden"
        except PaperlessMcpError:
            return None, "failed"

    async def _user(self) -> int | None:
        """The current user's id, or None if it cannot be read (then tasks are not filtered)."""
        try:
            settings = await self._client.get(SETTINGS, _Settings)
        except PaperlessMcpError:
            return None
        return settings.user.id

    @staticmethod
    def _filters(kind: str, user: int | None) -> dict[str, str | int]:
        """The task list filters: the kind and, for files Paperless consumes, the user's own.

        ``ordering=-id`` is ignored by Paperless's task list; ``-date_created`` is its order.
        """
        params: dict[str, str | int] = {"task_type": kind, "ordering": "-date_created"}
        if kind == CONSUME and user is not None:
            params["owner"] = user
        return params

    async def _newest(self, kind: str, user: int | None) -> _Task | None:
        """The newest task of this user and kind, None if there is none."""
        page = await self._client.get(
            TASKS, Page[_Task], params={**self._filters(kind, user), "page_size": 1}
        )
        return page.results[0] if page.results else None

    async def _tasks_after(
        self, mark: _Task | None, kind: str, user: int | None, match: Match | None
    ) -> list[_Task]:
        """The tasks of this user and kind newer than ``mark`` that ``match``, oldest first."""
        params: dict[str, str | int] = {**self._filters(kind, user), "page_size": LOOKBACK}
        if mark is not None:
            params["date_created_after"] = mark.date_created
        page = await self._client.get(TASKS, Page[_Task], params=params)
        newer = (t for t in page.results if mark is None or t.id > mark.id)
        mine = (t for t in newer if kind != CONSUME or user is None or t.owner == user)
        return sorted(
            (t for t in mine if t.task_type == kind and (match is None or match(t))),
            key=lambda t: t.id,
        )


def _ids(found: list[_Task], expected: int) -> tuple[str, ...]:
    """The ids of the oldest ``expected`` tasks: later ones belong to other requests."""
    return tuple(task.task_id for task in found[:expected])
