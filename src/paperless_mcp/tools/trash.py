"""The trash toolset: what was deleted, getting it back, and emptying it (opt-in).

Deleting a document (``delete_document``, ``bulk_edit_documents``) or a version
(``delete_document_version``) moves it to the Paperless trash, where it stays until the trash is
emptied (Paperless does so by itself after 30 days by default). ``restore_from_trash`` undoes a
deletion and needs no confirmation. ``empty_trash`` removes entries for good, so it never
defaults to anything: it needs document_ids or all=true, and confirm in both cases (decision-6,
decision-15).
"""

from typing import Annotated

from fastmcp import Context
from fastmcp.server.providers import LocalProvider
from pydantic import Field

from paperless_mcp.domain.trash import TrashEmptied, TrashList, TrashRestored
from paperless_mcp.services.trash import MAX_IDS
from paperless_mcp.tools._shared import ANNOTATIONS, Confirm, boundary, services

TOOLSET = "trash"

provider = LocalProvider()

type PositiveId = Annotated[int, Field(ge=1)]

LIST_LIMIT = 100
PAGE_SIZE = 25


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["list_trash"])
@boundary
async def list_trash(
    ctx: Context,
    *,
    page: Annotated[int, Field(ge=1, description="Page number, from 1.")] = 1,
    page_size: Annotated[
        int, Field(ge=1, le=LIST_LIMIT, description="Entries per page, at most 100.")
    ] = PAGE_SIZE,
) -> TrashList:
    """List what was deleted and can still be restored: documents and deleted versions.

    An entry has an id (pass it to restore_from_trash or empty_trash), the title and the time it
    was deleted. A version deleted from a document is an entry of its own, with version_of set
    to the document; while the document itself is in the trash, its entry covers the versions.
    You see the entries of documents you own or that have no owner; a superuser sees every entry.
    web_url is the trash page of the web UI. count is the total; pass next_page as page to see
    more. Needs the permission to view documents.
    """
    return await services(ctx).trash.list(page=page, page_size=page_size)


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["restore_from_trash"])
@boundary
async def restore_from_trash(
    ctx: Context,
    document_ids: Annotated[
        list[PositiveId],
        Field(
            min_length=1,
            max_length=MAX_IDS,
            description=f"Entry ids from list_trash; at most {MAX_IDS}, each in the trash.",
        ),
    ],
) -> TrashRestored:
    """Take entries out of the trash: a document comes back whole, a version is re-attached.

    A restored document is back with its notes, versions, tags and the rest of its metadata. A
    restored version becomes a version of its document again, with its label, and the newest
    one if it was. All or none: an id that is not in the trash (see list_trash) is an error and
    nothing is restored; so is a version whose document is in the trash too, restore the
    document instead. Needs the permission to delete documents and ownership of every document
    (or no owner). Needs no confirm: nothing is lost.
    """
    return await services(ctx).trash.restore(document_ids)


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["empty_trash"])
@boundary
async def empty_trash(
    ctx: Context,
    *,
    document_ids: Annotated[
        list[PositiveId] | None,
        Field(
            min_length=1,
            max_length=MAX_IDS,
            description=(
                f"Entry ids from list_trash to delete for good; at most {MAX_IDS}. Use this or "
                "all, never both."
            ),
        ),
    ] = None,
    all: Annotated[  # noqa: A002 -- the parameter's name in the tool schema
        bool,
        Field(
            description="true: delete every entry in the trash for good. Use this or document_ids."
        ),
    ] = False,
    confirm: Confirm = False,
) -> TrashEmptied:
    """Delete trash entries for good: documents, their files, text, notes and metadata are lost.

    This cannot be undone. It never empties everything by default: name document_ids from
    list_trash, or pass all=true for the whole trash, and in both cases confirm. Two steps: call
    without confirm first. Nothing is deleted and the error lists the entries that would go
    (the first ten, then a count), with confirm=true to pass. all=true empties whatever the trash
    holds when the confirmed call runs, which can differ from what the refusal listed: when the
    trash holds at most 100 entries, list it with list_trash and pass their ids as document_ids
    instead, so exactly the reviewed entries go. Only name what the user asked to delete; to get
    documents back, use restore_from_trash. All or none: an id that is not in the trash is an
    error. Needs the permission to delete documents and ownership of every document (or no
    owner).
    """
    return await services(ctx).trash.empty(document_ids, everything=all, confirm=confirm)
