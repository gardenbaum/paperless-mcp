"""TrashReader and TrashWriter on ``/api/trash/``.

``GET`` lists the deleted documents the user may see, paginated, each as the full document row
plus ``deleted_at`` (the schema documents no body). A version deleted from a document is a row of
its own: its ``id`` is the version's, ``root_document`` the document's and ``title`` the
document's. A document in the trash covers its versions, which are then not listed. ``POST``
takes ``{action: restore|empty, documents: [ids]}`` and answers
``{"result": "OK", "doc_ids": [...]}`` once done. Probed on 3.4.2:

- An id that is not in the trash, or is listed twice, is a 400 for the whole request.
- A request without ``documents`` empties everything the user sees (``empty``) or is a 500
  (``restore``); an empty list does nothing. Both are why this adapter never sends fewer than one
  id.
- A restored version is re-attached to its document (with its label), and a restored document
  brings its versions back. A version whose document is also in the trash is a 400.
- The global ``delete_document`` permission is a 403 (JSON); a document the user may not delete is
  a 403 with the text/html body ``Insufficient permissions``.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel

from paperless_mcp.domain.errors import NotFound, PermissionDenied, Validation
from paperless_mcp.domain.trash import TrashedDocument, TrashPage
from paperless_mcp.paperless.pagination import Page

if TYPE_CHECKING:
    from collections.abc import Sequence

    from paperless_mcp.paperless.client import PaperlessClient

PATH = "/api/trash/"


class _Row(BaseModel):
    id: int
    title: str
    deleted_at: datetime
    root_document: int | None


class _Done(BaseModel):
    result: str


def beyond_last_page(page: int) -> NotFound:
    """The error for a page past the end of the trash."""
    return NotFound(
        f"Page {page} of the trash does not exist.",
        hint="List the trash with page=1; count and next_page tell how far it goes.",
    )


def refused() -> PermissionDenied:
    """The error for a 403 of ``POST``: the permission or a document's ownership is missing."""
    return PermissionDenied(
        "The Paperless user may not restore or empty these entries.",
        hint=(
            "It needs the permission to delete documents and must own every document (or it "
            "must have no owner)."
        ),
    )


def _entry(row: _Row) -> TrashedDocument:
    return TrashedDocument(
        id=row.id, title=row.title, deleted_at=row.deleted_at, version_of=row.root_document
    )


class PaperlessTrashReader:
    """``TrashReader`` over the Paperless REST API."""

    def __init__(self, client: PaperlessClient) -> None:
        """Read through ``client``."""
        self._client: PaperlessClient = client

    async def list_trash(self, *, page: int, page_size: int) -> TrashPage:
        """See ``TrashReader.list_trash``."""
        try:
            found = await self._client.get(
                PATH, Page[_Row], params={"page": page, "page_size": page_size}
            )
        except NotFound:
            raise beyond_last_page(page) from None
        return TrashPage(
            count=found.count,
            next_page=found.next_page,
            entries=tuple(_entry(row) for row in found.results),
        )


class PaperlessTrashWriter:
    """``TrashWriter`` over the Paperless REST API."""

    def __init__(self, client: PaperlessClient) -> None:
        """Write through ``client``."""
        self._client: PaperlessClient = client

    async def restore_from_trash(self, document_ids: Sequence[int]) -> None:
        """See ``TrashWriter.restore_from_trash``."""
        await self._post("restore", document_ids)

    async def empty_trash(self, document_ids: Sequence[int]) -> None:
        """See ``TrashWriter.empty_trash``."""
        await self._post("empty", document_ids)

    async def _post(self, action: str, document_ids: Sequence[int]) -> None:
        if not document_ids:
            message = f"Name at least one trash entry to {action}."
            raise Validation(message, hint="Pass the ids from list_trash.")
        body = {"action": action, "documents": list(document_ids)}
        try:
            _ = await self._client.call("POST", PATH, _Done, json=body)
        except PermissionDenied:
            raise refused() from None
        except Validation as error:
            raise Validation(
                error.message, hint="Check the ids with list_trash; each must be in the trash."
            ) from None
