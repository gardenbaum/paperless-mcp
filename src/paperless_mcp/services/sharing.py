"""Sharing: public share URLs, bundles and e-mail, each saying what it publishes (design §6).

A share link or bundle is a public URL on the Paperless web UI (``{public url}/share/{slug}``);
this service builds the full URL, so the caller can hand it out as is. Expiry is given in days and
turned into a date here, because bundles take days and links take a date. Deleting and e-mailing
go through the confirmation flow (decision-6, decision-15): the refusal names the links or the
recipients and documents. Everything Paperless would refuse that this server can see beforehand
is refused first, so the caller is never asked to confirm what cannot happen.
"""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

from paperless_mcp.domain.errors import (
    ConfirmationRequired,
    FeatureUnavailable,
    NotFound,
    PermissionDenied,
    Validation,
    quoted,
)
from paperless_mcp.domain.sharing import (
    SHARE_ROUTE,
    EmailSent,
    ShareBundleInfo,
    ShareBundleListing,
    SharedDocument,
    ShareDeletion,
    ShareLinkInfo,
    ShareLinkListing,
)
from paperless_mcp.services.documents import document_link

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from paperless_mcp.domain.ports import DocumentReader, SharingReader, SharingWriter
    from paperless_mcp.domain.sharing import FileVersionKind, ShareBundle, ShareLink

# Longest expiry: ten years, which keeps the date arithmetic far from its limits.
MAX_DAYS: Final = 3650
# Most documents one bundle or e-mail takes: bounded, so texts and reads stay small.
MAX_DOCUMENTS: Final = 50
MAX_RECIPIENTS: Final = 20
RECIPIENT_HINT: Final = "Give plain addresses such as name@example.com."
PUBLIC_WARNING: Final = "anyone with the URL can download it without logging in"
EXCERPT: Final = 80


def share_url(web_url: str, slug: str) -> str:
    """The public URL of the share with ``slug`` under the Paperless web UI at ``web_url``."""
    return f"{web_url.rstrip('/')}/{SHARE_ROUTE}/{slug}"


def plural(count: int, noun: str) -> str:
    """``count`` and ``noun`` with an s unless there is exactly one."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def joined(parts: Sequence[str]) -> str:
    """The parts as prose: ``a``, ``a and b``, ``a, b and c``."""
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


def mail_disabled() -> FeatureUnavailable:
    """The error for an e-mail when Paperless has no mail server set up."""
    return FeatureUnavailable(
        "Paperless has no mail server set up, so it cannot send e-mail.",
        hint="An administrator sets PAPERLESS_EMAIL_HOST and the related variables.",
    )


def file_phrase(kind: FileVersionKind, count: int) -> str:
    """The files a share or e-mail hands out, in words."""
    noun = "file" if count == 1 else "files"
    if kind == "archive":
        return f"archive {noun} (the original where there is none)"
    return f"original {noun}"


def expiry_phrase(days: int | None) -> str:
    """When a link or bundle stops working, in words."""
    return "never expires" if days is None else f"expires in {plural(days, 'day')}"


def _days(days: int | None) -> int | None:
    if days is not None and not 1 <= days <= MAX_DAYS:
        message = f"expires_in_days must be between 1 and {MAX_DAYS}; {days} is not."
        raise Validation(message, hint="Leave it out for no expiry.")
    return days


def _unique(ids: Sequence[int]) -> list[int]:
    return list(dict.fromkeys(ids))


def _page[T](items: Sequence[T], page: int, page_size: int) -> tuple[Sequence[T], int | None]:
    """The items of ``page`` and the next page number, None on the last.

    Raises:
        NotFound: ``page`` is beyond the last page.
    """
    start = (page - 1) * page_size
    if page > 1 and start >= len(items):
        last = max(1, -(-len(items) // page_size))
        message = f"There is no page {page}: the list has {plural(last, 'page')}."
        raise NotFound(message, hint="Start again at page 1.")
    more = start + page_size < len(items)
    return items[start : start + page_size], page + 1 if more else None


class SharingService:
    """Share links, bundles and e-mail for the current user."""

    def __init__(
        self,
        reader: SharingReader,
        writer: SharingWriter,
        documents: DocumentReader,
        *,
        web_url: str,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Read through ``reader``, write through ``writer``, name documents through ``documents``.

        ``web_url`` is the public base URL of Paperless; ``now`` is the clock expiry counts from.
        """
        self._reader: SharingReader = reader
        self._writer: SharingWriter = writer
        self._documents: DocumentReader = documents
        self._web_url: str = web_url
        self._now: Callable[[], datetime] = now

    def _link(self, link: ShareLink) -> ShareLinkInfo:
        return ShareLinkInfo(
            id=link.id,
            url=share_url(self._web_url, link.slug),
            document_id=link.document_id,
            document_title=link.document_title,
            web_url=None
            if link.document_id is None
            else document_link(self._web_url, link.document_id),
            file_version=link.file_version,
            created=link.created,
            expiration=link.expiration,
            expired=self._expired(link.expiration),
        )

    async def _bundles(self, bundles: Sequence[ShareBundle]) -> tuple[ShareBundleInfo, ...]:
        """The bundles with their documents named, by one read of all the titles."""
        ids = sorted({number for bundle in bundles for number in bundle.document_ids})
        try:
            titles = await self._documents.get_document_titles(ids) if ids else {}
        except PermissionDenied:
            titles = {}  # a user who may not view documents still sees the ids
        return tuple(self._bundle(bundle, titles) for bundle in bundles)

    def _bundle(self, bundle: ShareBundle, titles: Mapping[int, str]) -> ShareBundleInfo:
        return ShareBundleInfo(
            id=bundle.id,
            url=share_url(self._web_url, bundle.slug),
            status=bundle.status,
            documents=tuple(
                SharedDocument(
                    id=number,
                    title=titles.get(number),
                    web_url=document_link(self._web_url, number),
                )
                for number in bundle.document_ids
            ),
            document_count=len(bundle.document_ids),
            file_version=bundle.file_version,
            size_bytes=bundle.size_bytes,
            last_error=bundle.last_error,
            created=bundle.created,
            built_at=bundle.built_at,
            expiration=bundle.expiration,
            expired=self._expired(bundle.expiration),
        )

    def _expired(self, expiration: datetime | None) -> bool:
        return expiration is not None and expiration <= self._now()

    async def _titles(self, ids: Sequence[int]) -> dict[int, str]:
        """The titles of the documents; the first one the user cannot see is an error.

        Raises:
            NotFound: A document does not exist or is hidden from the user.
            PermissionDenied: The user may not view documents.
        """
        titles = await self._documents.get_document_titles(ids)
        for number in ids:
            if number not in titles:
                message = f"No document {number} exists that this user may see."
                raise NotFound(message, hint="Use search_documents to find the document.")
        return titles

    async def list_links(
        self, document_id: int | None, *, page: int, page_size: int
    ) -> ShareLinkListing:
        """One page of the share links, those of one document if ``document_id`` is given.

        Raises:
            NotFound: ``page`` is beyond the last page.
            PermissionDenied: The user may not view share links.
        """
        links = [
            link
            for link in await self._reader.list_share_links()
            if document_id in {None, link.document_id}
        ]
        shown, following = _page(links, page, page_size)
        return ShareLinkListing(
            count=len(links),
            page=page,
            next_page=following,
            links=tuple(self._link(link) for link in shown),
        )

    async def create_link(
        self,
        document_id: int,
        *,
        expires_in_days: int | None,
        file_version: FileVersionKind,
        confirm: bool,
    ) -> ShareLinkInfo:
        """Publish the file of a document under a new public link; confirm names what is public.

        Raises:
            ConfirmationRequired: ``confirm`` is false. Nothing was published.
            Validation: ``expires_in_days`` is out of range.
            NotFound: The document does not exist or is hidden from the user.
            PermissionDenied: The user may not add share links.
        """
        days = _days(expires_in_days)
        titles = await self._titles([document_id])
        if not confirm:
            effect = (
                f"publish the {file_phrase(file_version, 1)} of "
                f"{quoted(titles[document_id])} (id {document_id}) under a public share link "
                f"that {expiry_phrase(days)}; {PUBLIC_WARNING}"
            )
            raise ConfirmationRequired.for_effect(effect)
        expiration = None if days is None else self._now() + timedelta(days=days)
        made = await self._writer.create_share_link(
            document_id, expiration=expiration, file_version=file_version
        )
        return self._link(made)

    async def delete_link(self, link_id: int, *, confirm: bool) -> ShareDeletion:
        """Delete a share link; without ``confirm`` refuse and say whose access ends.

        Raises:
            ConfirmationRequired: ``confirm`` is false. Nothing was changed.
            NotFound: No such link.
            PermissionDenied: The user may not delete share links, or this one.
        """
        link = await self._reader.get_share_link(link_id)
        subject = f"share link {link.id}"
        if link.document_id is not None:
            subject += f" for the document {quoted(link.document_title)} (id {link.document_id})"
        if not confirm:
            effect = f"delete {subject}: its URL stops working for everyone who has it"
            raise ConfirmationRequired.for_effect(effect)
        await self._writer.delete_share_link(link_id)
        message = f"{subject[0].upper()}{subject[1:]} was deleted; its URL no longer works."
        return ShareDeletion(id=link_id, message=message)

    async def list_bundles(self, *, page: int, page_size: int) -> ShareBundleListing:
        """One page of the bundles with their build state.

        Raises:
            NotFound: ``page`` is beyond the last page.
            PermissionDenied: The user may not view bundles.
        """
        bundles = await self._reader.list_share_bundles()
        shown, following = _page(bundles, page, page_size)
        return ShareBundleListing(
            count=len(bundles),
            page=page,
            next_page=following,
            bundles=await self._bundles(shown),
        )

    async def create_bundle(
        self,
        document_ids: Sequence[int],
        *,
        expires_in_days: int | None,
        file_version: FileVersionKind,
        confirm: bool,
    ) -> ShareBundleInfo:
        """Publish the files of several documents as one public archive; confirm names them.

        Raises:
            ConfirmationRequired: ``confirm`` is false. Nothing was published.
            Validation: No documents, too many, or ``expires_in_days`` out of range.
            NotFound: A document does not exist or is hidden from the user.
            PermissionDenied: The user may not add bundles.
        """
        ids = _unique(document_ids)
        if not ids:
            message = "A bundle needs at least one document."
            raise Validation(message, hint="Find the documents with search_documents.")
        if len(ids) > MAX_DOCUMENTS:
            count = plural(MAX_DOCUMENTS, "document")
            message = f"A bundle takes at most {count}; {len(ids)} given."
            raise Validation(message, hint="Split it into several bundles.")
        days = _days(expires_in_days)
        titles = await self._titles(ids)
        if not confirm:
            named = joined([f"{quoted(titles[number])} (id {number})" for number in ids])
            effect = (
                f"publish the {file_phrase(file_version, len(ids))} of {named} as one ZIP "
                f"archive under a public share link that {expiry_phrase(days)}; "
                f"{PUBLIC_WARNING}"
            )
            raise ConfirmationRequired.for_effect(effect)
        made = await self._writer.create_share_bundle(
            ids, expiration_days=days, file_version=file_version
        )
        [shown] = await self._bundles([made])
        return shown

    async def rebuild_bundle(self, bundle_id: int) -> ShareBundleInfo:
        """Build the archive of a bundle again; the URL stays the same.

        Raises:
            NotFound: No such bundle.
            Validation: Paperless refused the rebuild, with its reason.
            PermissionDenied: The user may not change this bundle.
        """
        [shown] = await self._bundles([await self._writer.rebuild_share_bundle(bundle_id)])
        return shown

    async def delete_bundle(self, bundle_id: int, *, confirm: bool) -> ShareDeletion:
        """Delete a bundle with its archive; without ``confirm`` refuse and say what ends.

        Raises:
            ConfirmationRequired: ``confirm`` is false. Nothing was changed.
            NotFound: No such bundle.
            PermissionDenied: The user may not delete bundles, or this one.
        """
        bundle = await self._reader.get_share_bundle(bundle_id)
        subject = f"share-link bundle {bundle.id} of {plural(len(bundle.document_ids), 'document')}"
        if not confirm:
            effect = (
                f"delete {subject} with its archive: its URL stops working for everyone who has it"
            )
            raise ConfirmationRequired.for_effect(effect)
        await self._writer.delete_share_bundle(bundle_id)
        message = f"{subject[0].upper()}{subject[1:]} was deleted; its URL no longer works."
        return ShareDeletion(id=bundle_id, message=message)

    async def email(  # noqa: PLR0913 -- the parts of a message
        self,
        document_ids: Sequence[int],
        *,
        recipients: Sequence[str],
        subject: str,
        message: str,
        use_archive_version: bool,
        confirm: bool,
    ) -> EmailSent:
        """E-mail the files of documents; without ``confirm`` refuse naming recipients and files.

        Raises:
            ConfirmationRequired: ``confirm`` is false. Nothing was sent.
            Validation: No or too many documents or recipients, an address that is none, an empty
                subject or message.
            NotFound: A document does not exist or is hidden from the user.
            FeatureUnavailable: Paperless has no mail server set up.
            DeliveryUnknown: Paperless failed while sending; whether anyone got it is unknown.
            PermissionDenied: The user may not view documents.
        """
        ids = _unique(document_ids)
        addresses = self._addresses(recipients)
        self._check_text(ids, subject, message)
        if await self._reader.get_email_enabled() is False:
            raise mail_disabled()
        titles = await self._titles(ids)
        kind: FileVersionKind = "archive" if use_archive_version else "original"
        if not confirm:
            named = joined([f"{quoted(titles[number])} (id {number})" for number in ids])
            together = (
                " (all of them in one To: header, so each sees the others)"
                if len(addresses) > 1
                else ""
            )
            effect = (
                f"send an e-mail with the {file_phrase(kind, len(ids))} of {named} to "
                f"{joined(addresses)}{together}, subject {quoted(subject)}, message "
                f"{quoted(message, EXCERPT)}; it leaves Paperless and cannot be recalled"
            )
            raise ConfirmationRequired.for_effect(effect)
        await self._writer.email_documents(
            ids,
            addresses=addresses,
            subject=subject.strip(),
            message=message.strip(),
            use_archive_version=use_archive_version,
        )
        files = f"{kind} {'file' if len(ids) == 1 else 'files'}"
        done = f"Sent the {files} of {plural(len(ids), 'document')}"
        return EmailSent(
            recipients=tuple(addresses),
            documents=tuple(ids),
            message=f"{done} to {joined(addresses)}.",
        )

    @staticmethod
    def _check_text(ids: Sequence[int], subject: str, message: str) -> None:
        """Refuse an e-mail without documents or text, or with too many documents.

        Raises:
            Validation: Any of those.
        """
        problem, hint = None, "Send several e-mails."
        if not ids:
            problem, hint = "An e-mail needs at least one document.", "Use search_documents."
        elif len(ids) > MAX_DOCUMENTS:
            count = plural(MAX_DOCUMENTS, "document")
            problem = f"An e-mail takes at most {count}; {len(ids)} given."
        elif not subject.strip():
            problem, hint = "The subject is empty.", "Give the e-mail a subject."
        elif not message.strip():
            problem, hint = "The message is empty.", "Write the text of the e-mail."
        if problem is not None:
            raise Validation(problem, hint=hint)

    @staticmethod
    def _addresses(recipients: Sequence[str]) -> list[str]:
        """The addresses, split at commas, trimmed, without duplicates (ignoring case).

        Raises:
            Validation: None, too many, or one that cannot be an address.
        """
        found: dict[str, str] = {}
        for entry in recipients:
            for part in entry.split(","):
                address = part.strip()
                if not address:
                    continue
                if "@" not in address or any(c.isspace() for c in address):
                    message = f"{quoted(address)} is not an e-mail address."
                    raise Validation(message, hint=RECIPIENT_HINT)
                _ = found.setdefault(address.casefold(), address)
        if not found:
            message = "An e-mail needs at least one recipient."
            raise Validation(message, hint=RECIPIENT_HINT)
        if len(found) > MAX_RECIPIENTS:
            count = plural(MAX_RECIPIENTS, "recipient")
            message = f"An e-mail takes at most {count}; {len(found)} given."
            raise Validation(message, hint="Send several e-mails.")
        return list(found.values())
