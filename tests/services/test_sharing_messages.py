"""The exact words of the errors the sharing service gives, and the edges of its limits.

One recorded snapshot of the messages and hints, so a changed word is a deliberate change that
shows up in review (and a mutated literal cannot go unnoticed); plain tests for the boundaries.
"""

from datetime import timedelta
from typing import TYPE_CHECKING, final, override

import pytest
from fakes.paperless import FakePaperless, seeded

from paperless_mcp.domain.errors import PaperlessMcpError
from paperless_mcp.domain.sharing import ShareLink
from paperless_mcp.paperless.sharing import no_answer, send_failed, unseen_bundle, unseen_link
from paperless_mcp.services.sharing import (
    MAX_DAYS,
    MAX_DOCUMENTS,
    MAX_RECIPIENTS,
    SharingService,
    expiry_phrase,
    file_phrase,
    joined,
    plural,
    share_url,
)
from services.test_sharing import NOW, Setup

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from syrupy.assertion import SnapshotAssertion

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-15")]

MAIL = {"recipients": ["a@example.com"], "subject": "s", "message": "m"}


async def said(action: Callable[[], Awaitable[object]]) -> str:
    try:
        _ = await action()
    except PaperlessMcpError as error:
        return error.describe()
    return "accepted"


async def test_the_messages_of_the_sharing_service(snapshot: SnapshotAssertion) -> None:
    setup = Setup(mail=True)
    service = setup.service
    _ = await service.create_link(1, expires_in_days=None, file_version="archive", confirm=True)
    for _ in range(2):
        _ = await service.create_bundle(
            [1, 2], expires_in_days=None, file_version="archive", confirm=True
        )

    async def email(**changes: object) -> object:
        arguments: dict[str, object] = {
            "documents": [1],
            "use_archive_version": True,
            "confirm": False,
            **MAIL,
            **changes,
        }
        return await service.email(
            arguments["documents"],  # pyright: ignore[reportArgumentType] -- a test table
            recipients=arguments["recipients"],  # pyright: ignore[reportArgumentType] -- see above
            subject=str(arguments["subject"]),
            message=str(arguments["message"]),
            use_archive_version=bool(arguments["use_archive_version"]),
            confirm=bool(arguments["confirm"]),
        )

    said_by_case = {
        "days too few": await said(
            lambda: service.create_link(1, expires_in_days=0, file_version="archive", confirm=True)
        ),
        "days too many": await said(
            lambda: service.create_link(
                1, expires_in_days=MAX_DAYS + 1, file_version="archive", confirm=True
            )
        ),
        "document hidden": await said(
            lambda: service.create_link(
                99, expires_in_days=None, file_version="archive", confirm=True
            )
        ),
        "page too far": await said(lambda: service.list_links(None, page=2, page_size=25)),
        "page too far, many": await said(lambda: service.list_bundles(page=9, page_size=1)),
        "bundle empty": await said(
            lambda: service.create_bundle(
                [], expires_in_days=None, file_version="archive", confirm=True
            )
        ),
        "bundle too big": await said(
            lambda: service.create_bundle(
                list(range(1, MAX_DOCUMENTS + 2)),
                expires_in_days=None,
                file_version="archive",
                confirm=True,
            )
        ),
        "bundle days": await said(
            lambda: service.create_bundle(
                [1], expires_in_days=0, file_version="archive", confirm=True
            )
        ),
        "mail no documents": await said(lambda: email(documents=[])),
        "mail too many documents": await said(
            lambda: email(documents=list(range(1, MAX_DOCUMENTS + 2)))
        ),
        "mail no subject": await said(lambda: email(subject=" ")),
        "mail no message": await said(lambda: email(message=" ")),
        "mail no recipients": await said(lambda: email(recipients=[])),
        "mail bad recipient": await said(lambda: email(recipients=["nobody"])),
        "mail too many recipients": await said(
            lambda: email(recipients=[f"u{n}@example.com" for n in range(MAX_RECIPIENTS + 1)])
        ),
        "mail disabled": await said(
            lambda: Setup(mail=False).service.email(
                [1],
                recipients=["a@example.com"],
                subject="s",
                message="m",
                use_archive_version=True,
                confirm=False,
            )
        ),
        "mail hidden document": await said(lambda: email(documents=[99])),
        "mail confirm": await said(email),
        "mail confirm, two documents": await said(lambda: email(documents=[1, 2])),
        "mail confirm, two recipients": await said(
            lambda: email(recipients=["a@example.com", "b@example.com"])
        ),
        "mail confirm, long text": await said(lambda: email(subject="s" * 80, message="m" * 200)),
        "mail confirm, original": await said(lambda: email(use_archive_version=False)),
        "link confirm": await said(
            lambda: service.create_link(1, expires_in_days=7, file_version="archive", confirm=False)
        ),
        "link confirm, original, never": await said(
            lambda: service.create_link(
                1, expires_in_days=None, file_version="original", confirm=False
            )
        ),
        "bundle confirm": await said(
            lambda: service.create_bundle(
                [1, 2], expires_in_days=1, file_version="archive", confirm=False
            )
        ),
        "bundle confirm, one document, original": await said(
            lambda: service.create_bundle(
                [1], expires_in_days=None, file_version="original", confirm=False
            )
        ),
        "link delete confirm": await said(lambda: service.delete_link(1, confirm=False)),
        "bundle delete confirm": await said(lambda: service.delete_bundle(1, confirm=False)),
    }

    assert said_by_case == snapshot


async def test_the_messages_of_the_adapter_errors(snapshot: SnapshotAssertion) -> None:
    assert [
        e.describe() for e in (send_failed(), no_answer(), unseen_link(3), unseen_bundle(4))
    ] == snapshot


@final
class Orphaned(FakePaperless):
    """A Paperless whose only link has lost its document."""

    @override
    async def list_share_links(self) -> tuple[ShareLink, ...]:
        return (
            ShareLink(
                id=1,
                document_id=None,
                document_title="",
                slug="s",
                created=NOW,
                expiration=None,
                file_version="archive",
            ),
        )

    @override
    async def get_share_link(self, link_id: int) -> ShareLink:
        return (await self.list_share_links())[0]


async def test_a_link_without_a_document_has_no_document_link_and_is_named_alone() -> None:
    fake = Orphaned(seeded(), user="admin")
    service = SharingService(fake, fake, fake, web_url="https://p.example", now=lambda: NOW)

    [link] = (await service.list_links(None, page=1, page_size=25)).links
    with pytest.raises(PaperlessMcpError) as raised:
        _ = await service.delete_link(1, confirm=False)

    assert (link.web_url, link.document_id) == (None, None)
    assert raised.value.message == (
        "Nothing was changed. Confirming would delete share link 1: its URL stops working "
        "for everyone who has it."
    )


@pytest.mark.parametrize("days", [1, 2, MAX_DAYS - 1, MAX_DAYS])
async def test_days_inside_the_range_are_accepted(days: int) -> None:
    setup = Setup()

    made = await setup.service.create_link(
        1, expires_in_days=days, file_version="archive", confirm=True
    )

    assert made.expiration == NOW + timedelta(days=days)
    bundle = await setup.service.create_bundle(
        [1], expires_in_days=days, file_version="archive", confirm=True
    )
    assert bundle.expiration == NOW + timedelta(days=days)


async def test_a_share_that_expires_exactly_now_has_expired() -> None:
    setup = Setup()
    _ = await setup.service.create_link(1, expires_in_days=1, file_version="archive", confirm=True)
    _ = await setup.service.create_bundle(
        [1], expires_in_days=1, file_version="archive", confirm=True
    )
    setup.state.share_links[0].expiration = NOW
    setup.state.share_bundles[0].expiration = NOW

    link = (await setup.service.list_links(None, page=1, page_size=25)).links[0]
    bundle = (await setup.service.list_bundles(page=1, page_size=25)).bundles[0]

    assert (link.expired, bundle.expired) == (True, True)


async def test_the_bundle_listing_carries_the_build_time_and_expiry() -> None:
    setup = Setup()
    _ = await setup.service.create_bundle(
        [1], expires_in_days=2, file_version="archive", confirm=True
    )
    _ = await setup.service.list_bundles(page=1, page_size=25)  # the build completes

    [bundle] = (await setup.service.list_bundles(page=1, page_size=25)).bundles

    assert bundle.built_at == NOW
    assert bundle.expiration == NOW + timedelta(days=2)
    assert bundle.expired is False


async def test_a_page_that_starts_right_after_the_last_item_is_missing() -> None:
    setup = Setup()
    for _ in range(3):
        _ = await setup.service.create_bundle(
            [1], expires_in_days=None, file_version="archive", confirm=True
        )

    with pytest.raises(PaperlessMcpError, match="no page 2: the list has 1 page"):
        _ = await setup.service.list_bundles(page=2, page_size=3)


@pytest.mark.parametrize(
    ("count", "page_size", "page", "next_page"),
    [(3, 3, 1, None), (4, 3, 1, 2), (3, 2, 2, None), (6, 2, 3, None), (7, 2, 3, 4)],
)
async def test_paging_reaches_every_item_and_stops_at_the_last(
    count: int, page_size: int, page: int, next_page: int | None
) -> None:
    setup = Setup()
    for _ in range(count):
        _ = await setup.service.create_bundle(
            [1], expires_in_days=None, file_version="archive", confirm=True
        )

    listing = await setup.service.list_bundles(page=page, page_size=page_size)

    assert listing.next_page == next_page
    assert listing.page == page
    assert len(listing.bundles) == min(page_size, count - (page - 1) * page_size)


async def test_the_first_page_of_an_empty_list_is_empty_not_missing() -> None:
    setup = Setup()

    listing = await setup.service.list_links(None, page=1, page_size=25)

    assert (listing.count, listing.links, listing.next_page) == (0, (), None)


async def test_exactly_the_most_documents_and_recipients_are_accepted() -> None:
    setup = Setup(mail=True)
    ids = setup.documents(MAX_DOCUMENTS)
    people = [f"u{n}@example.com" for n in range(MAX_RECIPIENTS)]

    bundle = await setup.service.create_bundle(
        ids, expires_in_days=None, file_version="archive", confirm=True
    )
    sent = await setup.service.email(
        ids,
        recipients=people,
        subject="s",
        message="m",
        use_archive_version=True,
        confirm=True,
    )

    assert bundle.document_count == MAX_DOCUMENTS
    assert len(sent.recipients) == MAX_RECIPIENTS


async def test_a_blank_entry_among_recipients_is_skipped_not_the_rest() -> None:
    setup = Setup(mail=True)

    sent = await setup.service.email(
        [1],
        recipients=[" ", ", a@example.com"],
        subject="s",
        message="m",
        use_archive_version=True,
        confirm=True,
    )

    assert sent.recipients == ("a@example.com",)


def test_prose_helpers() -> None:
    assert joined([]) == ""
    assert joined(["a"]) == "a"
    assert joined(["a", "b"]) == "a and b"
    assert plural(1, "page") == "1 page"
    assert plural(2, "page") == "2 pages"
    assert share_url("https://p.example//", "s") == "https://p.example/share/s"
    assert share_url("https://pX/", "s") == "https://pX/share/s"


def test_the_words_for_files_and_expiry() -> None:
    assert file_phrase("archive", 1) == "archive file (the original where there is none)"
    assert file_phrase("archive", 2) == "archive files (the original where there is none)"
    assert file_phrase("original", 1) == "original file"
    assert file_phrase("original", 3) == "original files"
    assert expiry_phrase(None) == "never expires"
    assert expiry_phrase(1) == "expires in 1 day"
    assert expiry_phrase(30) == "expires in 30 days"
