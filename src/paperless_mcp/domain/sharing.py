"""Sharing documents with people outside Paperless: share links, share-link bundles, e-mail.

A share link is a public URL for the file of one document; anyone who holds it can read the file
until it expires or is deleted. A bundle is one such URL for the files of several documents, which
Paperless packs into a ZIP archive in the background. E-mail sends the files to addresses through
the mail server Paperless is set up with. Nothing here needs a login on the receiving side, so the
tools say plainly what they publish.
"""

from datetime import datetime
from typing import Annotated, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

# Which file of a document a link, bundle or e-mail hands out.
type FileVersionKind = Literal["archive", "original"]
type BundleStatus = Literal["pending", "processing", "ready", "failed"]

# The route of the Paperless web UI that serves a share: ``{web url}/share/{slug}``.
SHARE_ROUTE: Final = "share"


class ShareLink(BaseModel):
    """A share link as Paperless stores it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    document_id: int | None
    document_title: str
    slug: str
    created: datetime
    expiration: datetime | None
    file_version: FileVersionKind


class ShareBundle(BaseModel):
    """A share-link bundle as Paperless stores it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    slug: str
    created: datetime
    expiration: datetime | None
    file_version: FileVersionKind
    status: BundleStatus
    size_bytes: int | None
    last_error: str | None
    built_at: datetime | None
    document_ids: tuple[int, ...]


# Output models are tool output schemas: a one-line class docstring each, and field descriptions
# only where the name does not say enough.

PUBLIC = "Anyone who has this URL can open the file without logging in."


class ShareLinkInfo(BaseModel):
    """A share link of one document, as a full URL."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    url: Annotated[str, Field(description=PUBLIC)]
    document_id: int | None
    document_title: str
    web_url: Annotated[
        str | None, Field(description="Link to the document in the Paperless web UI.")
    ]
    file_version: FileVersionKind
    created: datetime
    expiration: Annotated[datetime | None, Field(description="When it stops working; null: never.")]
    expired: Annotated[bool, Field(description="Whether it has stopped working already.")]


class ShareLinkListing(BaseModel):
    """One page of share links."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    count: Annotated[int, Field(description="How many links match in total.")]
    page: int
    next_page: Annotated[int | None, Field(description="The next page, null on the last one.")]
    links: tuple[ShareLinkInfo, ...]


class SharedDocument(BaseModel):
    """A document in a bundle."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    title: Annotated[str | None, Field(description="Null if the user may not see the document.")]
    web_url: Annotated[str, Field(description="Link to the document in the Paperless web UI.")]


class ShareBundleInfo(BaseModel):
    """A share-link bundle: one URL for a ZIP archive of several documents."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    url: Annotated[
        str,
        Field(description=f"{PUBLIC} It serves the archive once the status is ready."),
    ]
    status: Annotated[
        BundleStatus,
        Field(
            description=(
                "pending or processing: Paperless is building the archive; ready: the URL works; "
                "failed: see last_error and call rebuild_share_bundle."
            )
        ),
    ]
    documents: tuple[SharedDocument, ...]
    document_count: int
    file_version: FileVersionKind
    size_bytes: Annotated[int | None, Field(description="The archive size once built.")]
    last_error: Annotated[str | None, Field(description="Why the last build failed, if it did.")]
    created: datetime
    built_at: datetime | None
    expiration: Annotated[datetime | None, Field(description="When it stops working; null: never.")]
    expired: Annotated[bool, Field(description="Whether it has stopped working already.")]


class ShareBundleListing(BaseModel):
    """One page of share-link bundles."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    count: Annotated[int, Field(description="How many bundles exist in total.")]
    page: int
    next_page: Annotated[int | None, Field(description="The next page, null on the last one.")]
    bundles: tuple[ShareBundleInfo, ...]


class ShareDeletion(BaseModel):
    """The outcome of a confirmed delete of a share link or bundle."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    message: Annotated[str, Field(description="What was deleted.")]


class EmailSent(BaseModel):
    """The outcome of email_documents: Paperless accepted the message for sending."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    recipients: tuple[str, ...]
    documents: Annotated[tuple[int, ...], Field(description="The ids of the attached documents.")]
    message: Annotated[str, Field(description="What was sent and to whom.")]
