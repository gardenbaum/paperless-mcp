"""SharingReader and SharingWriter on the share link, bundle and e-mail endpoints.

The paths are ``/api/share_links/``, ``/api/share_link_bundles/`` and ``/api/documents/email/``.

Paperless answers 403 to a user without the global permission for the model, and it filters the
lists by the permissions of the objects. A bundle build runs in the background: a create or a
rebuild answers ``pending``. A rebuild ignores its request body (probed on 3.4.2), but the schema
requires one, so it sends the documents the bundle already has. Sending e-mail is never
retried: a repeated request could send the message twice.
"""

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel

from paperless_mcp.domain.errors import (
    DeliveryUnknown,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    UpstreamTimeout,
    UpstreamUnavailable,
    UpstreamUnreachable,
)
from paperless_mcp.domain.sharing import BundleStatus, FileVersionKind, ShareBundle, ShareLink
from paperless_mcp.paperless.pagination import MAX_ITEMS, Page, collect

if TYPE_CHECKING:
    from collections.abc import Sequence

    from paperless_mcp.paperless.client import PaperlessClient

LINKS: Final = "/api/share_links/"
BUNDLES: Final = "/api/share_link_bundles/"
EMAIL: Final = "/api/documents/email/"
UI_SETTINGS: Final = "/api/ui_settings/"
# Paperless sends the mail inside the request, so it can take long (seconds per attachment).
EMAIL_TIMEOUT: Final = 120.0
# Statuses that mean the request was refused before anything was sent: throttling only. A 503
# can come from a proxy after it forwarded the request, so it does not prove that nothing went out.
NOT_SENT: Final = frozenset({429})
MAX_ERROR = 200


class _LinkRow(BaseModel):
    id: int
    created: datetime
    expiration: datetime | None
    slug: str
    document: int | None = None
    document_title: str
    file_version: FileVersionKind


class _BundleRow(BaseModel):
    id: int
    created: datetime
    expiration: datetime | None
    slug: str
    file_version: FileVersionKind
    status: BundleStatus
    size_bytes: int | None
    last_error: object = None
    built_at: datetime | None
    documents: list[int]


class _MailSettings(BaseModel):
    email_enabled: bool | None = None


class _Ui(BaseModel):
    settings: _MailSettings


class _Sent(BaseModel):
    message: str | None = None


def unseen_link(link_id: int) -> NotFound:
    """The error for a share link that does not exist or that the user may not see."""
    message = f"No share link {link_id} exists that this user may see."
    return NotFound(message, hint="Call list_share_links to see the links you can use.")


def unseen_bundle(bundle_id: int) -> NotFound:
    """The error for a bundle that does not exist or that the user may not see."""
    message = f"No share-link bundle {bundle_id} exists that this user may see."
    return NotFound(message, hint="Call list_share_bundles to see the bundles you can use.")


def send_failed() -> DeliveryUnknown:
    """The error for an e-mail during whose sending Paperless failed."""
    return DeliveryUnknown(
        "Paperless failed while sending the e-mail; whether any recipient got it is unknown.",
        hint=(
            "Check the Paperless log and ask the user before sending again: Paperless sends "
            "synchronously, so a repeat can send it twice."
        ),
    )


def no_answer() -> DeliveryUnknown:
    """The error for an e-mail request that got no answer."""
    return DeliveryUnknown(
        "Paperless did not answer; whether the e-mail was sent is unknown.",
        hint=(
            "Check with the user and the Paperless log before sending again: Paperless sends "
            "synchronously, so a repeat can send it twice."
        ),
    )


def _link(row: _LinkRow) -> ShareLink:
    return ShareLink(
        id=row.id,
        document_id=row.document,
        document_title=row.document_title,
        slug=row.slug,
        created=row.created,
        expiration=row.expiration,
        file_version=row.file_version,
    )


def _error_text(error: object) -> str | None:
    """The build error as one bounded line: Paperless stores it as free-form JSON."""
    if error is None:
        return None
    if isinstance(error, str):
        text = error
    elif isinstance(error, dict) and isinstance(error.get("message"), str):  # pyright: ignore[reportUnknownMemberType] -- JSON
        text = str(error["message"])  # pyright: ignore[reportUnknownArgumentType] -- JSON
    else:
        text = json.dumps(error)
    line = " ".join(text.split())
    return line if len(line) <= MAX_ERROR else line[: MAX_ERROR - 1] + "…"


def _bundle(row: _BundleRow) -> ShareBundle:
    return ShareBundle(
        id=row.id,
        slug=row.slug,
        created=row.created,
        expiration=row.expiration,
        file_version=row.file_version,
        status=row.status,
        size_bytes=row.size_bytes,
        last_error=_error_text(row.last_error),
        built_at=row.built_at,
        document_ids=tuple(row.documents),
    )


class PaperlessSharingReader:
    """``SharingReader`` over the Paperless REST API."""

    def __init__(self, client: PaperlessClient) -> None:
        """Read through ``client``."""
        self._client: PaperlessClient = client

    async def list_share_links(self) -> tuple[ShareLink, ...]:
        """See ``SharingReader.list_share_links``."""
        collected = await collect(self._client, LINKS, Page[_LinkRow])
        if collected.truncated:
            message = (
                f"Paperless has more than {MAX_ITEMS} share links; this server reads up to that."
            )
            raise PaperlessMcpError(message)
        return tuple(_link(row) for row in collected.items)

    async def get_share_link(self, link_id: int) -> ShareLink:
        """See ``SharingReader.get_share_link``."""
        try:
            return _link(await self._client.get(f"{LINKS}{link_id}/", _LinkRow))
        except NotFound:
            raise unseen_link(link_id) from None

    async def list_share_bundles(self) -> tuple[ShareBundle, ...]:
        """See ``SharingReader.list_share_bundles``."""
        collected = await collect(self._client, BUNDLES, Page[_BundleRow])
        if collected.truncated:
            message = (
                f"Paperless has more than {MAX_ITEMS} share-link bundles; "
                "this server reads up to that."
            )
            raise PaperlessMcpError(message)
        return tuple(_bundle(row) for row in collected.items)

    async def get_email_enabled(self) -> bool | None:
        """See ``SharingReader.get_email_enabled``."""
        try:
            return (await self._client.get(UI_SETTINGS, _Ui)).settings.email_enabled
        except PermissionDenied:
            return None

    async def get_share_bundle(self, bundle_id: int) -> ShareBundle:
        """See ``SharingReader.get_share_bundle``."""
        try:
            return _bundle(await self._client.get(f"{BUNDLES}{bundle_id}/", _BundleRow))
        except NotFound:
            raise unseen_bundle(bundle_id) from None


class PaperlessSharingWriter:
    """``SharingWriter`` over the Paperless REST API."""

    def __init__(self, client: PaperlessClient) -> None:
        """Write through ``client``."""
        self._client: PaperlessClient = client

    async def create_share_link(
        self, document_id: int, *, expiration: datetime | None, file_version: FileVersionKind
    ) -> ShareLink:
        """See ``SharingWriter.create_share_link``."""
        body = {
            "document": document_id,
            "expiration": None
            if expiration is None
            else expiration.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "file_version": file_version,
        }
        return _link(await self._client.call("POST", LINKS, _LinkRow, json=body))

    async def delete_share_link(self, link_id: int) -> None:
        """See ``SharingWriter.delete_share_link``."""
        try:
            _ = await self._client.request("DELETE", f"{LINKS}{link_id}/", retry=False)
        except NotFound:
            raise unseen_link(link_id) from None

    async def create_share_bundle(
        self,
        document_ids: Sequence[int],
        *,
        expiration_days: int | None,
        file_version: FileVersionKind,
    ) -> ShareBundle:
        """See ``SharingWriter.create_share_bundle``."""
        body: dict[str, object] = {"document_ids": list(document_ids), "file_version": file_version}
        if expiration_days is not None:
            body["expiration_days"] = expiration_days
        return _bundle(await self._client.call("POST", BUNDLES, _BundleRow, json=body))

    async def rebuild_share_bundle(self, bundle_id: int) -> ShareBundle:
        """See ``SharingWriter.rebuild_share_bundle``."""
        try:
            current = await self._client.get(f"{BUNDLES}{bundle_id}/", _BundleRow)
            row = await self._client.call(
                "POST",
                f"{BUNDLES}{bundle_id}/rebuild/",
                _BundleRow,
                json={"document_ids": current.documents},
                retry=False,
            )
        except NotFound:
            raise unseen_bundle(bundle_id) from None
        return _bundle(row)

    async def delete_share_bundle(self, bundle_id: int) -> None:
        """See ``SharingWriter.delete_share_bundle``."""
        try:
            _ = await self._client.request("DELETE", f"{BUNDLES}{bundle_id}/", retry=False)
        except NotFound:
            raise unseen_bundle(bundle_id) from None

    async def email_documents(
        self,
        document_ids: Sequence[int],
        *,
        addresses: Sequence[str],
        subject: str,
        message: str,
        use_archive_version: bool,
    ) -> None:
        """See ``SharingWriter.email_documents``: never retried, with a longer timeout."""
        body = {
            "documents": list(document_ids),
            "addresses": ",".join(addresses),
            "subject": subject,
            "message": message,
            "use_archive_version": use_archive_version,
        }
        try:
            _ = await self._client.call(
                "POST", EMAIL, _Sent, json=body, timeout=EMAIL_TIMEOUT, retry=False
            )
        except UpstreamTimeout, UpstreamUnreachable:
            raise no_answer() from None
        except UpstreamUnavailable as error:
            if error.status in NOT_SENT:
                raise
            raise send_failed() from None
