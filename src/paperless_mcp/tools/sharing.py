"""The sharing toolset: share links, share-link bundles and e-mail (opt-in, off by default).

These tools publish documents to people outside Paperless, so they say what they publish. A share
link is a public URL for the file of one document and a bundle one URL for a ZIP archive of
several; anyone who has the URL can read the files, with no login, until it expires or is deleted.
``email_documents`` sends the files to addresses through the mail server Paperless is set up
with, and cannot be recalled. Creating a link or bundle, deleting one and sending e-mail all
need ``confirm`` (decision-6, decision-15, decision-17): the refusal says what would become
public or leave.
"""

from typing import Annotated

from fastmcp import Context
from fastmcp.server.providers import LocalProvider
from pydantic import Field

from paperless_mcp.domain.sharing import (
    EmailSent,
    FileVersionKind,
    ShareBundleInfo,
    ShareBundleListing,
    ShareDeletion,
    ShareLinkInfo,
    ShareLinkListing,
)
from paperless_mcp.services.sharing import MAX_DAYS, MAX_DOCUMENTS, MAX_RECIPIENTS
from paperless_mcp.tools._shared import ANNOTATIONS, Confirm, boundary, services

TOOLSET = "sharing"
PAGE_SIZE = 25
LIST_LIMIT = 100

provider = LocalProvider()

type PositiveId = Annotated[int, Field(ge=1)]
type DocumentId = Annotated[PositiveId, Field(description="Document id, from search_documents.")]
type DocumentIds = Annotated[
    list[PositiveId],
    Field(
        min_length=1,
        max_length=MAX_DOCUMENTS,
        description=f"Document ids, from search_documents; at most {MAX_DOCUMENTS}, each once.",
    ),
]
type Expiry = Annotated[
    int | None,
    Field(
        ge=1,
        le=MAX_DAYS,
        description="It stops working this many days from now; leave out: it never expires.",
    ),
]
type FileChoice = Annotated[
    FileVersionKind,
    Field(
        description=(
            "archive: the searchable PDF/A Paperless made (the original where there is none); "
            "original: the file as it was uploaded."
        )
    ),
]
type Page = Annotated[int, Field(ge=1, description="Page number, from 1.")]
type Size = Annotated[int, Field(ge=1, le=LIST_LIMIT, description="Rows per page, at most 100.")]
type LinkId = Annotated[PositiveId, Field(description="Share link id, from list_share_links.")]
type BundleId = Annotated[PositiveId, Field(description="Bundle id, from list_share_bundles.")]


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["list_share_links"])
@boundary
async def list_share_links(
    ctx: Context,
    document_id: Annotated[
        PositiveId | None, Field(description="Only the links of this document; default: all.")
    ] = None,
    page: Page = 1,
    page_size: Size = PAGE_SIZE,
) -> ShareLinkListing:
    """List the public share links of documents, as full URLs, with their expiry.

    Each row has the URL, the document, which file it serves, when it expires and whether it
    already has. Needs the view permission for share links in Paperless. Create one with
    create_share_link, remove one with delete_share_link. count is the total; pass next_page as
    page to see more.
    """
    return await services(ctx).sharing.list_links(document_id, page=page, page_size=page_size)


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["create_share_link"])
@boundary
async def create_share_link(
    ctx: Context,
    document_id: DocumentId,
    expires_in_days: Expiry = None,
    file_version: FileChoice = "archive",
    *,
    confirm: Confirm = False,
) -> ShareLinkInfo:
    """Publish the file of one document under a new public link and return its full URL.

    ANYONE WHO HAS THE URL can open the file without logging in, until the link expires or is
    deleted with delete_share_link; it can be passed on and cannot be recalled from people who
    saved the file. Without confirm it publishes nothing and fails with an error that names the
    document, the file, the expiry and what is public; show that to the user and call again with
    confirm=true only once they agree. Needs the add permission for share links and a document
    the user can see. Creating again makes another link. Use create_share_bundle for several
    documents at once.
    """
    return await services(ctx).sharing.create_link(
        document_id, expires_in_days=expires_in_days, file_version=file_version, confirm=confirm
    )


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["delete_share_link"])
@boundary
async def delete_share_link(
    ctx: Context,
    link_id: LinkId,
    *,
    confirm: Confirm = False,
) -> ShareDeletion:
    """Delete a share link: its URL stops working for everyone who has it. The document stays.

    Without confirm it deletes nothing and fails with an error that says which link and document
    are affected; describe that to the user and call again with confirm=true once they agree.
    """
    return await services(ctx).sharing.delete_link(link_id, confirm=confirm)


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["list_share_bundles"])
@boundary
async def list_share_bundles(
    ctx: Context, page: Page = 1, page_size: Size = PAGE_SIZE
) -> ShareBundleListing:
    """List the share-link bundles (one public URL for a ZIP archive of several documents).

    Each row has the full URL, the documents, the build status (pending and processing: Paperless
    is still building the archive; ready: the URL works; failed: last_error says why, and
    rebuild_share_bundle tries again), the size and the expiry. count is the total; pass
    next_page as page to see more.
    """
    return await services(ctx).sharing.list_bundles(page=page, page_size=page_size)


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["create_share_bundle"])
@boundary
async def create_share_bundle(
    ctx: Context,
    document_ids: DocumentIds,
    expires_in_days: Expiry = None,
    file_version: FileChoice = "archive",
    *,
    confirm: Confirm = False,
) -> ShareBundleInfo:
    """Publish the files of several documents as one ZIP archive under a public link.

    ANYONE WHO HAS THE URL can download the archive without logging in, until the bundle expires
    or is deleted with delete_share_bundle. Paperless builds the archive after this returns, so
    the status is usually pending: the URL works once list_share_bundles shows ready. Without
    confirm it publishes nothing and fails with an error that names the documents, the files,
    the expiry and what is public; show that to the user and call again with confirm=true only
    once they agree. Needs the add permission for bundles and documents the user can see.
    """
    return await services(ctx).sharing.create_bundle(
        document_ids,
        expires_in_days=expires_in_days,
        file_version=file_version,
        confirm=confirm,
    )


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["rebuild_share_bundle"])
@boundary
async def rebuild_share_bundle(
    ctx: Context,
    bundle_id: BundleId,
) -> ShareBundleInfo:
    """Build the archive of a bundle again, for example after a failed build.

    The URL and the expiry stay as they are, and the same public URL then serves the documents'
    current files (a changed document is published again in its new form); the status goes back
    to pending until Paperless has built the archive (check list_share_bundles).
    """
    return await services(ctx).sharing.rebuild_bundle(bundle_id)


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["delete_share_bundle"])
@boundary
async def delete_share_bundle(
    ctx: Context,
    bundle_id: BundleId,
    *,
    confirm: Confirm = False,
) -> ShareDeletion:
    """Delete a bundle and its archive: its URL stops working for everyone who has it.

    The documents stay. Without confirm it deletes nothing and fails with an error that says
    which bundle is affected; describe that to the user and call again with confirm=true once
    they agree.
    """
    return await services(ctx).sharing.delete_bundle(bundle_id, confirm=confirm)


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["email_documents"])
@boundary
async def email_documents(  # noqa: PLR0913 -- the parts of a message
    ctx: Context,
    document_ids: DocumentIds,
    recipients: Annotated[
        list[str],
        Field(
            min_length=1,
            max_length=MAX_RECIPIENTS,
            description=f"E-mail addresses, at most {MAX_RECIPIENTS}; commas inside one are split.",
        ),
    ],
    subject: Annotated[str, Field(min_length=1, description="The subject line.")],
    message: Annotated[str, Field(min_length=1, description="The text of the e-mail.")],
    *,
    use_archive_version: Annotated[
        bool,
        Field(
            description="Attach the archive file where Paperless made one; false: the originals."
        ),
    ] = True,
    confirm: Confirm = False,
) -> EmailSent:
    """Send the files of documents by e-mail through the mail server of Paperless.

    This SENDS THE FILES to outside addresses and cannot be recalled. All recipients appear in
    one To: header, so each sees the others. Without confirm it sends nothing and fails with an
    error that names the recipients, the documents, the subject and the start of the message;
    show that to the user and call again with confirm=true only once they agree. Paperless needs
    a mail server set up by its administrator; without one the call fails before asking. If it
    fails while sending, whether anyone got the mail is unknown: check with the user and the
    Paperless log before sending again, because a repeat can send it twice.
    """
    return await services(ctx).sharing.email(
        document_ids,
        recipients=recipients,
        subject=subject,
        message=message,
        use_archive_version=use_archive_version,
        confirm=confirm,
    )
