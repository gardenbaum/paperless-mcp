"""The pdf toolset: server-side PDF operations (opt-in, not in the default profile).

Paperless 3.x rotates, merges, splits and edits PDFs itself and removes their passwords. The work
runs as background tasks: every tool returns them (``wait_seconds`` waits for them, up to the
usual bound). Making new documents, and adding a version that keeps every page, change nothing
that exists; deleting originals, dropping pages from what a document shows and reprocessing do,
so they need ``confirm`` (decision-6, decision-15). Page numbers count from 1.
"""

from typing import Annotated, Literal

from fastmcp import Context
from fastmcp.server.providers import LocalProvider
from pydantic import Field

from paperless_mcp.domain.pdf import Output, PageSlot, PdfResult
from paperless_mcp.services.pdf import MAX_DOCUMENTS, MAX_PAGES, MAX_PARTS
from paperless_mcp.services.tasks import MAX_WAIT_SECONDS
from paperless_mcp.tools._shared import ANNOTATIONS, Confirm, boundary, services

TOOLSET = "pdf"

provider = LocalProvider()

type DocumentId = Annotated[int, Field(description="Document id, from search_documents.")]
type Wait = Annotated[
    int,
    Field(
        ge=0,
        description=(
            f"Wait up to this many seconds for the tasks Paperless starts and return their "
            f"outcome; more than {MAX_WAIT_SECONDS} is cut to {MAX_WAIT_SECONDS}. 0 returns at "
            "once with the task ids; follow them with get_task."
        ),
    ),
]
type Where = Annotated[
    Output,
    Field(
        description=(
            "Where the result goes: new_document (the original stays untouched), new_version "
            "(the same document shows the new file; the older version stays) or "
            "replace_original (a new document, and the original moves to the Paperless trash)."
        )
    ),
]

TASKS = (
    "Paperless does the work later, in background tasks: the result lists the ones it could "
    "find (state, produced_ids: the new documents, or for a new version its id) and with "
    "wait_seconds waits for them. "
)
OWNERS = (
    "Needs the permission to change documents and ownership of every document (or no owner): "
    "a change grant is not enough. A new document also needs the permission to add documents, "
    "and trashing an original the permission to delete documents. Only PDF files."
)
MERGERS = (
    "Needs the permission to change and to add documents and a change grant on (or ownership "
    "of) every document; delete_originals also needs ownership of them and the permission to "
    "delete documents. Only PDF files."
)
COMMON = TASKS + OWNERS
MERGE_NEEDS = TASKS + MERGERS


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["rotate_documents"])
@boundary
async def rotate_documents(
    ctx: Context,
    document_ids: Annotated[
        list[int],
        Field(
            min_length=1,
            max_length=MAX_DOCUMENTS,
            description=f"Ids of the PDFs to rotate, at most {MAX_DOCUMENTS}, each once.",
        ),
    ],
    degrees: Annotated[
        Literal[90, 180, 270], Field(description="Degrees to turn every page clockwise.")
    ],
    *,
    wait_seconds: Wait = 0,
) -> PdfResult:
    """Rotate every page of PDF documents clockwise.

    Each document gets the rotated file as a new version and the older version stays, so
    nothing is lost and no confirm is needed. The document keeps its id, title and metadata.
    Needs the permission to change documents and ownership of every document (or no owner): a
    change grant is not enough.
    """
    return await services(ctx).pdf.rotate(document_ids, degrees, wait_seconds=wait_seconds)


MERGE = (
    "Merge PDF documents, in the order given, into one new document. The new document takes "
    "the metadata (title with ' (merged)', tags, correspondent, ...) of metadata_document_id "
    "if given. The originals stay unless delete_originals is true: they then move to the "
    "Paperless trash, from where they can be restored, which needs confirm. Two steps for "
    "that: call without confirm first; nothing is merged and the error names the documents "
    "and what would happen, with confirm=true to pass. "
)


@provider.tool(
    tags={TOOLSET},
    annotations=ANNOTATIONS["merge_documents"],
    description=MERGE + MERGE_NEEDS,
)
@boundary
async def merge_documents(  # noqa: PLR0913 -- the documents and the options of the merge
    ctx: Context,
    document_ids: Annotated[
        list[int],
        Field(
            min_length=2,
            max_length=MAX_DOCUMENTS,
            description=f"Ids of the PDFs to merge, in order, at most {MAX_DOCUMENTS}, each once.",
        ),
    ],
    *,
    metadata_document_id: Annotated[
        int | None,
        Field(description="One of the merged documents whose metadata the new one takes."),
    ] = None,
    delete_originals: Annotated[
        bool, Field(description="true moves the merged documents to the Paperless trash.")
    ] = False,
    confirm: Confirm = False,
    wait_seconds: Wait = 0,
) -> PdfResult:
    """Merge PDF documents into one new document; the tool description is on the decorator."""
    return await services(ctx).pdf.merge(
        document_ids,
        metadata_document_id,
        delete_originals=delete_originals,
        confirm=confirm,
        wait_seconds=wait_seconds,
    )


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["split_document"])
@boundary
async def split_document(  # noqa: PLR0913 -- the document and the options of the split
    ctx: Context,
    document_id: DocumentId,
    parts: Annotated[
        list[str],
        Field(
            min_length=2,
            max_length=MAX_PARTS,
            description=(
                "One page range per new document, e.g. ['1-3', '4,6-8']: pages and ranges "
                "separated by commas, '5-' runs to the last page. A page belongs to one part "
                "at most; pages in no part are in no new document."
            ),
        ),
    ],
    *,
    output: Annotated[
        Literal["new_document", "replace_original"],
        Field(
            description=(
                "new_document leaves the original; replace_original moves it to the Paperless "
                "trash afterwards and needs confirm."
            )
        ),
    ] = "new_document",
    confirm: Confirm = False,
    wait_seconds: Wait = 0,
) -> PdfResult:
    """Split a PDF into one new document per page range.

    The new documents take the title and metadata of the original. The original stays unless
    output is replace_original: it then moves to the Paperless trash, from where it can be
    restored, which needs confirm. Two steps for that: call without confirm first; nothing is
    split and the error names the document and what would happen, with confirm=true to pass. Page
    ranges are checked against the page count first. Needs the permission to change and to add
    documents and ownership of the document (or no owner; a change grant is not enough), and for
    replace_original the permission to delete documents.
    """
    return await services(ctx).pdf.split(
        document_id, parts, output=output, confirm=confirm, wait_seconds=wait_seconds
    )


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["delete_pages"])
@boundary
async def delete_pages(  # noqa: PLR0913 -- the document and the options of the deletion
    ctx: Context,
    document_id: DocumentId,
    pages: Annotated[
        str,
        Field(
            description=(
                "The pages to delete, counted from 1: pages and ranges separated by commas, "
                "e.g. '2' or '1-3,5'. Not every page."
            )
        ),
    ],
    *,
    output: Where = "new_version",
    confirm: Confirm = False,
    wait_seconds: Wait = 0,
) -> PdfResult:
    """Delete pages from a PDF by making a file without them.

    By default (new_version) the document then shows the shorter file and the older version with
    all pages stays until it is deleted, but pages vanish from what the document shows, so this
    needs confirm; so does replace_original (a new document without the pages, the original
    moves to the Paperless trash). With new_document nothing existing changes and no confirm is
    needed. Two steps: call without confirm first. Nothing is deleted and the error names the
    pages and what would happen, with confirm=true to pass. Page ranges are checked against the
    page count first. Needs the permission to change documents (and to add them for a new
    document) and ownership of the document (or no owner; a change grant is not enough).
    """
    return await services(ctx).pdf.delete_pages(
        document_id, pages, output=output, confirm=confirm, wait_seconds=wait_seconds
    )


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["edit_pdf"])
@boundary
async def edit_pdf(  # noqa: PLR0913 -- the document and the options of the edit
    ctx: Context,
    document_id: DocumentId,
    pages: Annotated[
        list[PageSlot],
        Field(
            min_length=1,
            max_length=MAX_PAGES,
            description=(
                "The pages of the result in order. Each entry names the source page (from 1), "
                "how far to turn it clockwise (rotate: 0, 90, 180, 270) and which output file "
                "it goes to (document: 0, 1, 2 ... without gaps). A page may appear twice."
            ),
        ),
    ],
    *,
    output: Where = "new_document",
    confirm: Confirm = False,
    wait_seconds: Wait = 0,
) -> PdfResult:
    """Build PDF files from the pages of one document: pick, reorder, rotate and group pages.

    The general form of split_document, delete_pages and rotate_documents. With
    output new_document (the default) the original stays and every output file becomes a new
    document with the title and metadata of the original. new_version needs a single output file
    and makes it the file the document shows; if it leaves pages out, that needs confirm, as
    does replace_original (new documents, and the original moves to the Paperless trash). Two
    steps for those: call without confirm first. Nothing is changed and the error says what
    would happen, with confirm=true to pass. Page numbers are checked against the page count
    first. Needs the permission to change documents (and to add them for new documents) and
    ownership of the document (or no owner; a change grant is not enough).
    """
    return await services(ctx).pdf.edit(
        document_id, pages, output=output, confirm=confirm, wait_seconds=wait_seconds
    )


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["remove_document_password"])
@boundary
async def remove_document_password(  # noqa: PLR0913 -- the document, the password and the options
    ctx: Context,
    document_id: DocumentId,
    password: Annotated[
        str,
        Field(
            min_length=1,
            description="The password that opens the PDF. It is sent to Paperless, never logged.",
        ),
    ],
    *,
    output: Where = "new_document",
    confirm: Confirm = False,
    wait_seconds: Wait = 0,
) -> PdfResult:
    """Remove the password from an encrypted PDF.

    Encrypted PDFs are stored without text, so unlocking them lets Paperless read them: the
    unlocked copy is consumed again and gets its text. By default it becomes a new document;
    new_version adds it to the same document (the locked file stays as an older version);
    replace_original makes a new document and moves the locked original to the Paperless trash,
    which needs confirm (call without it first: nothing changes and the error says what would
    happen). A wrong password is an error; a file without a password starts no task. Needs the
    permission to change documents and ownership of the document (or no owner; a change grant is
    not enough), and to add documents unless output is new_version.
    """
    return await services(ctx).pdf.remove_password(
        document_id, password, output=output, confirm=confirm, wait_seconds=wait_seconds
    )


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["reprocess_documents"])
@boundary
async def reprocess_documents(
    ctx: Context,
    document_ids: Annotated[
        list[int],
        Field(
            min_length=1,
            max_length=MAX_DOCUMENTS,
            description=f"Ids of the documents to reprocess, at most {MAX_DOCUMENTS}, each once.",
        ),
    ],
    *,
    remote_ocr: Annotated[
        bool, Field(description="true uses the remote OCR engine if Paperless has one set up.")
    ] = False,
    confirm: Confirm = False,
    wait_seconds: Wait = 0,
) -> PdfResult:
    """Run OCR and archiving again for documents, one background task each.

    The archived PDF and the extracted text are replaced by the new ones, so manual changes to
    the text are lost. Two steps: call without confirm first. Nothing is reprocessed and the
    error names the documents and what would happen, with confirm=true to pass. Use this for
    plain reprocessing, also with remote_ocr; the reprocess operation of bulk_edit_documents (if
    that toolset is on) does the same through Paperless's deprecated bulk form and adds a dry_run
    preview. Paperless names no document for a reprocess task, so the tasks in the result cannot
    be tied to the documents. Needs the permission to change documents and a change grant on (or
    ownership of) each.
    """
    return await services(ctx).pdf.reprocess(
        document_ids, remote_ocr=remote_ocr, confirm=confirm, wait_seconds=wait_seconds
    )
