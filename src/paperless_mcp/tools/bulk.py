"""The bulk toolset: one operation on many documents, or on many taxonomy objects, at once.

Opt-in (``PAPERLESS_MCP_TOOLSETS=core,bulk``). One wrong call can damage hundreds of documents,
so every operation can be previewed with ``dry_run`` first and executes only with ``confirm``
(design 5.4, decisions 6 and 15).
"""

from typing import Annotated

from fastmcp import Context
from fastmcp.server.providers import LocalProvider
from pydantic import Field

from paperless_mcp.domain.bulk import (
    MAX_DOCUMENTS,
    BulkKind,
    BulkOperation,
    DocumentBulkReport,
    TaxonomyBulkReport,
    TaxonomyOperation,
)
from paperless_mcp.domain.documents import NameOrId
from paperless_mcp.tools._shared import ANNOTATIONS, Confirm, boundary, services

TOOLSET = "bulk"

provider = LocalProvider()

type DryRun = Annotated[
    bool,
    Field(
        description=(
            "true returns a preview (how many change, a sample, what changes) and writes "
            "nothing; it needs no confirm. Do it first."
        )
    ),
]


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["bulk_edit_documents"])
@boundary
async def bulk_edit_documents(
    ctx: Context,
    document_ids: Annotated[
        list[int],
        Field(
            min_length=1,
            max_length=MAX_DOCUMENTS,
            description=(
                f"Ids of the documents to change, at most {MAX_DOCUMENTS}, from search_documents "
                "(page_size 100 fits); repeated ids count once."
            ),
        ),
    ],
    operation: BulkOperation,
    *,
    dry_run: DryRun = False,
    confirm: Confirm = False,
) -> DocumentBulkReport:
    """Change many documents in one step: tags, correspondent, type, storage path, custom fields,
    trash or reprocess.

    Three steps: call with dry_run=true to see how many documents change and a sample, then
    without dry_run and without confirm to get an error describing the effect, then with
    confirm=true to execute. Documents that already are as asked are counted and left alone.
    One operation per call, on documents you name by id. Tags are hierarchical: adding a child
    adds its parent, and removing a parent whose child tags stay is refused (list the children
    too). Custom fields are merged: only the fields you name change. Document links are mirrored:
    linking A to B also changes B (you need change permission there), so also_changed lists
    those documents; a shorter list does not unlink the old target (Paperless 3.4.2), removing
    the field does. delete moves documents to the Paperless trash; reprocess runs OCR again in
    the background. Paperless checks every document first: if you may not change one of them
    (blocked lists them), none changes and nothing is sent. Needs change permission on the
    documents, and ownership and the delete permission for delete.
    """  # noqa: D205 -- the summary is one sentence; FastMCP sends the docstring as is
    return await services(ctx).bulk_documents.edit(
        document_ids, operation, dry_run=dry_run, confirm=confirm
    )


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["bulk_edit_taxonomy"])
@boundary
async def bulk_edit_taxonomy(  # noqa: PLR0913 -- one parameter per concept, described for the LLM
    ctx: Context,
    kind: Annotated[
        BulkKind,
        Field(description="What the objects are: tag, correspondent, document_type, storage_path."),
    ],
    items: Annotated[
        list[NameOrId],
        Field(
            min_length=1,
            max_length=MAX_DOCUMENTS,
            description=f"Names or ids of the objects, at most {MAX_DOCUMENTS}; see list_taxonomy.",
        ),
    ],
    operation: TaxonomyOperation,
    *,
    dry_run: DryRun = False,
    confirm: Confirm = False,
) -> TaxonomyBulkReport:
    """Delete many tags, correspondents, document types or storage paths, or set who owns them
    and who may view or change them.

    Three steps, like bulk_edit_documents: dry_run=true previews, a call without confirm is an
    error that describes the effect, confirm=true executes. A delete is final (taxonomy has no
    trash): documents lose the reference, and deleting a tag deletes its child tags too, also
    ones you cannot see. set_permissions without merge replaces all grants with the lists you
    give (a list left out becomes empty); with merge it adds to them, and merge cannot set an
    owner. A change grant includes view. Users and groups by name or id. Custom fields are not
    supported. Paperless checks every object first: if you may not change one, none changes (you
    need the permission and must own it, or it must have no owner).
    """  # noqa: D205 -- the summary is one sentence; FastMCP sends the docstring as is
    return await services(ctx).bulk_taxonomy.edit(
        kind, items, operation, dry_run=dry_run, confirm=confirm
    )
