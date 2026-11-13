"""SharingService: public share URLs, expiry, confirmed deletes and the confirmed e-mail (PLMCP-26).

Fakes of the "unconfirmed" cases are created without `allow_writes`: the fake's write guard raises
on any write, so a passing test proves nothing was written or sent.
"""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, final, override

import pytest
from fakes.accounts import ADMIN
from fakes.paperless import FakeDocument, FakePaperless, FakeState, FakeUser, seeded

from paperless_mcp.domain.errors import (
    CONFIRM_HINT,
    ConfirmationRequired,
    FeatureUnavailable,
    NotFound,
    PermissionDenied,
    Validation,
)
from paperless_mcp.services.sharing import (
    MAX_DOCUMENTS,
    MAX_RECIPIENTS,
    SharingService,
    share_url,
)

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.decision("decision-6"),
    pytest.mark.decision("decision-15"),
]

if TYPE_CHECKING:
    from collections.abc import Collection

WEB = "https://docs.example.org/paperless"
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
PUBLIC_TITLE = "e2e-public-invoice"


@final
class Setup:
    def __init__(self, *, writes: bool = True, mail: bool = False) -> None:
        self.state: FakeState = seeded()
        self.state.email_enabled = mail
        self.fake: FakePaperless = FakePaperless(self.state, user=ADMIN[0], allow_writes=writes)
        self.service: SharingService = SharingService(
            self.fake, self.fake, self.fake, web_url=WEB, now=lambda: NOW
        )

    def documents(self, count: int) -> list[int]:
        start = max(d.id for d in self.state.documents) + 1
        for number in range(start, start + count):
            self.state.documents.append(FakeDocument(id=number, title=f"doc {number}", owner=2))
        return list(range(start, start + count))


def test_a_share_url_is_the_public_url_the_share_route_and_the_slug() -> None:
    assert share_url("https://p.example/", "abc") == "https://p.example/share/abc"
    assert share_url("https://p.example/sub", "abc") == "https://p.example/sub/share/abc"


# --- links --------------------------------------------------------------------------------------


async def test_a_created_link_is_a_full_url_on_the_public_url_with_its_expiry() -> None:
    setup = Setup()

    made = await setup.service.create_link(
        1, expires_in_days=7, file_version="original", confirm=True
    )

    stored = setup.state.share_links[0]
    assert made.url == f"{WEB}/share/{stored.slug}"
    assert made.expiration == NOW + timedelta(days=7)
    assert (made.expired, made.file_version) == (False, "original")
    assert (made.document_id, made.document_title) == (1, PUBLIC_TITLE)
    assert made.web_url == f"{WEB}/documents/1/details"
    assert stored.expiration == NOW + timedelta(days=7)


async def test_a_link_without_days_never_expires() -> None:
    setup = Setup()

    made = await setup.service.create_link(
        1, expires_in_days=None, file_version="archive", confirm=True
    )

    assert (made.expiration, made.expired) == (None, False)


@pytest.mark.parametrize("days", [0, -1, 3651])
async def test_days_outside_the_range_are_refused_before_anything_is_sent(days: int) -> None:
    setup = Setup(writes=False)

    with pytest.raises(Validation, match="expires_in_days"):
        _ = await setup.service.create_link(
            1, expires_in_days=days, file_version="archive", confirm=True
        )


async def test_a_link_for_a_document_the_user_cannot_see_says_so() -> None:
    setup = Setup()

    with pytest.raises(NotFound, match="No document 99 exists") as raised:
        _ = await setup.service.create_link(
            99, expires_in_days=None, file_version="archive", confirm=True
        )

    assert raised.value.hint == "Use search_documents to find the document."


async def test_the_links_are_listed_in_pages_and_can_be_limited_to_a_document() -> None:
    setup = Setup()
    [other] = setup.documents(1)
    for document in (1, 1, other):
        _ = await setup.service.create_link(
            document, expires_in_days=None, file_version="archive", confirm=True
        )
    setup.state.share_links[0].expiration = NOW - timedelta(days=1)

    first = await setup.service.list_links(None, page=1, page_size=2)
    second = await setup.service.list_links(None, page=2, page_size=2)
    mine = await setup.service.list_links(1, page=1, page_size=25)

    assert (first.count, first.page, first.next_page) == (3, 1, 2)
    assert (second.count, second.next_page, len(second.links)) == (3, None, 1)
    assert [link.id for link in first.links] == [1, 2]
    assert first.links[0].expired is True
    assert (mine.count, [link.document_id for link in mine.links]) == (2, [1, 1])


async def test_a_page_beyond_the_last_is_not_found() -> None:
    setup = Setup()

    with pytest.raises(NotFound, match="page 3"):
        _ = await setup.service.list_links(None, page=3, page_size=25)


async def test_deleting_a_link_without_confirm_says_who_loses_access_and_deletes_nothing() -> None:
    setup = Setup()
    made = await setup.service.create_link(
        1, expires_in_days=None, file_version="archive", confirm=True
    )
    guarded = SharingService(
        FakePaperless(setup.state, user=ADMIN[0]),
        FakePaperless(setup.state, user=ADMIN[0]),
        setup.fake,
        web_url=WEB,
        now=lambda: NOW,
    )

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await guarded.delete_link(made.id, confirm=False)

    assert raised.value.message == (
        f"Nothing was changed. Confirming would delete share link {made.id} for the document "
        f"'{PUBLIC_TITLE}' (id 1): its URL stops working for everyone who has it."
    )
    assert raised.value.hint == CONFIRM_HINT
    assert len(setup.state.share_links) == 1


async def test_a_confirmed_delete_removes_the_link_and_says_so() -> None:
    setup = Setup()
    made = await setup.service.create_link(
        1, expires_in_days=None, file_version="archive", confirm=True
    )

    gone = await setup.service.delete_link(made.id, confirm=True)

    assert gone.id == made.id
    assert gone.message == (
        f"Share link {made.id} for the document '{PUBLIC_TITLE}' (id 1) was deleted;"
        " its URL no longer works."
    )
    assert setup.state.share_links == []


# --- bundles ------------------------------------------------------------------------------------


async def test_a_created_bundle_is_pending_with_a_full_url_and_the_documents() -> None:
    setup = Setup()

    made = await setup.service.create_bundle(
        [2, 1, 2], expires_in_days=2, file_version="original", confirm=True
    )

    stored = setup.state.share_bundles[0]
    assert made.url == f"{WEB}/share/{stored.slug}"
    assert (made.status, made.document_count) == ("pending", 2)
    assert [(d.id, d.title, d.web_url) for d in made.documents] == [
        (1, PUBLIC_TITLE, f"{WEB}/documents/1/details"),
        (2, "e2e-private-note", f"{WEB}/documents/2/details"),
    ]
    assert made.file_version == "original"
    assert made.expiration == NOW + timedelta(days=2)
    assert stored.documents == (1, 2)


@pytest.mark.parametrize(
    ("documents", "message"),
    [
        pytest.param([], "at least one document", id="none"),
        pytest.param(list(range(1, MAX_DOCUMENTS + 2)), f"at most {MAX_DOCUMENTS}", id="too many"),
    ],
)
async def test_a_bundle_needs_one_to_the_most_documents(documents: list[int], message: str) -> None:
    setup = Setup(writes=False)

    with pytest.raises(Validation, match=message):
        _ = await setup.service.create_bundle(
            documents, expires_in_days=None, file_version="archive", confirm=True
        )


async def test_the_bundle_listing_shows_the_build_state_in_pages() -> None:
    setup = Setup()
    for _ in range(3):
        _ = await setup.service.create_bundle(
            [1], expires_in_days=None, file_version="archive", confirm=True
        )

    first = await setup.service.list_bundles(page=1, page_size=2)
    second = await setup.service.list_bundles(page=2, page_size=2)

    assert (first.count, first.next_page, [b.id for b in first.bundles]) == (3, 2, [1, 2])
    assert (second.next_page, [b.id for b in second.bundles]) == (None, [3])
    assert {b.status for b in first.bundles} == {"ready"}
    assert first.bundles[0].size_bytes is not None


async def test_a_rebuilt_bundle_comes_back_pending_with_the_same_url() -> None:
    setup = Setup()
    made = await setup.service.create_bundle(
        [1], expires_in_days=None, file_version="archive", confirm=True
    )
    _ = await setup.service.list_bundles(page=1, page_size=25)  # the build completes

    rebuilt = await setup.service.rebuild_bundle(made.id)

    assert (rebuilt.status, rebuilt.url) == ("pending", made.url)


async def test_a_failed_bundle_shows_why() -> None:
    setup = Setup()
    made = await setup.service.create_bundle(
        [1], expires_in_days=None, file_version="archive", confirm=True
    )
    setup.state.share_bundles[0].status = "failed"
    setup.state.share_bundles[0].last_error = "disk full"

    [shown] = (await setup.service.list_bundles(page=1, page_size=25)).bundles

    assert (shown.id, shown.status, shown.last_error) == (made.id, "failed", "disk full")


async def test_deleting_a_bundle_without_confirm_says_what_stops_working() -> None:
    setup = Setup()
    made = await setup.service.create_bundle(
        [1, 2], expires_in_days=None, file_version="archive", confirm=True
    )
    guarded = SharingService(
        FakePaperless(setup.state, user=ADMIN[0]),
        FakePaperless(setup.state, user=ADMIN[0]),
        setup.fake,
        web_url=WEB,
        now=lambda: NOW,
    )

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await guarded.delete_bundle(made.id, confirm=False)

    assert raised.value.message == (
        f"Nothing was changed. Confirming would delete share-link bundle {made.id} of 2 "
        "documents with its archive: its URL stops working for everyone who has it."
    )
    assert len(setup.state.share_bundles) == 1


async def test_a_confirmed_bundle_delete_removes_it() -> None:
    setup = Setup()
    made = await setup.service.create_bundle(
        [1], expires_in_days=None, file_version="archive", confirm=True
    )

    gone = await setup.service.delete_bundle(made.id, confirm=True)

    assert gone.message == (
        f"Share-link bundle {made.id} of 1 document was deleted; its URL no longer works."
    )
    assert setup.state.share_bundles == []


# --- e-mail -------------------------------------------------------------------------------------


async def test_an_unconfirmed_email_names_the_recipients_and_the_documents_and_sends_nothing() -> (
    None
):
    setup = Setup(writes=False, mail=True)

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.email(
            [1, 2],
            recipients=["a@example.com", " b@example.com "],
            subject="Invoices",
            message="Attached.",
            use_archive_version=True,
            confirm=False,
        )

    assert raised.value.message == (
        "Nothing was changed. Confirming would send an e-mail with the archive files (the "
        "original where there is none) of "
        "'e2e-public-invoice' (id 1) and 'e2e-private-note' (id 2) to a@example.com and "
        "b@example.com (all of them in one To: header, so each sees the others), "
        "subject 'Invoices', message 'Attached.'; it leaves Paperless and cannot be recalled."
    )
    assert raised.value.hint == CONFIRM_HINT
    assert setup.state.outbox == []


async def test_the_confirmation_names_the_original_files_when_those_are_sent() -> None:
    setup = Setup(writes=False, mail=True)

    with pytest.raises(ConfirmationRequired, match="the original file of 'e2e-public-invoice'"):
        _ = await setup.service.email(
            [1],
            recipients=["a@example.com"],
            subject="s",
            message="m",
            use_archive_version=False,
            confirm=False,
        )


async def test_a_confirmed_email_is_sent_to_every_recipient_once_with_the_file_choice() -> None:
    setup = Setup(mail=True)

    sent = await setup.service.email(
        [2, 1],
        recipients=["a@example.com", "A@example.com", "b@example.com, c@example.com"],
        subject="Invoices",
        message="Attached.",
        use_archive_version=False,
        confirm=True,
    )

    assert sent.recipients == ("a@example.com", "b@example.com", "c@example.com")
    assert sent.documents == (2, 1)
    assert sent.message == (
        "Sent the original files of 2 documents to a@example.com, b@example.com and c@example.com."
    )
    [mail] = setup.state.outbox
    assert mail.addresses == sent.recipients
    assert (mail.documents, mail.subject, mail.message) == ((2, 1), "Invoices", "Attached.")
    assert mail.use_archive_version is False


async def test_an_email_with_one_document_and_one_recipient_reads_in_the_singular() -> None:
    setup = Setup(mail=True)

    sent = await setup.service.email(
        [1],
        recipients=["a@example.com"],
        subject="s",
        message="m",
        use_archive_version=True,
        confirm=True,
    )

    assert sent.message == "Sent the archive file of 1 document to a@example.com."


async def test_an_email_where_paperless_has_no_mail_server_is_reported() -> None:
    setup = Setup(mail=False)

    with pytest.raises(FeatureUnavailable, match="no mail server set up"):
        _ = await setup.service.email(
            [1],
            recipients=["a@example.com"],
            subject="s",
            message="m",
            use_archive_version=True,
            confirm=True,
        )

    assert setup.state.outbox == []


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        pytest.param({"documents": []}, "at least one document", id="no documents"),
        pytest.param(
            {"documents": list(range(1, MAX_DOCUMENTS + 2))},
            f"at most {MAX_DOCUMENTS} documents",
            id="too many documents",
        ),
        pytest.param({"recipients": []}, "at least one recipient", id="no recipients"),
        pytest.param({"recipients": [" , "]}, "at least one recipient", id="blank recipients"),
        pytest.param(
            {"recipients": [f"u{n}@example.com" for n in range(MAX_RECIPIENTS + 1)]},
            f"at most {MAX_RECIPIENTS} recipients",
            id="too many recipients",
        ),
        pytest.param({"recipients": ["nobody"]}, "'nobody' is not an e-mail address", id="no @"),
        pytest.param({"recipients": ["a b@c.d"]}, "not an e-mail address", id="space"),
        pytest.param({"subject": "  "}, "subject", id="blank subject"),
        pytest.param({"message": ""}, "message", id="blank message"),
    ],
)
async def test_an_email_that_cannot_be_sent_is_refused_before_it_asks_for_confirmation(
    kwargs: dict[str, object], message: str
) -> None:
    setup = Setup(writes=False, mail=True)
    arguments: dict[str, object] = {
        "documents": [1],
        "recipients": ["a@example.com"],
        "subject": "s",
        "message": "m",
        **kwargs,
    }

    with pytest.raises(Validation, match=message) as raised:
        _ = await setup.service.email(
            arguments["documents"],  # pyright: ignore[reportArgumentType] -- built from the table
            recipients=arguments["recipients"],  # pyright: ignore[reportArgumentType] -- see above
            subject=str(arguments["subject"]),
            message=str(arguments["message"]),
            use_archive_version=True,
            confirm=False,
        )

    assert not isinstance(raised.value, ConfirmationRequired)


async def test_an_email_for_a_document_the_user_cannot_see_names_it() -> None:
    setup = Setup(writes=False, mail=True)

    with pytest.raises(NotFound, match="No document 99 exists"):
        _ = await setup.service.email(
            [1, 99],
            recipients=["a@example.com"],
            subject="s",
            message="m",
            use_archive_version=True,
            confirm=False,
        )


# --- confirmation of publishing (decision-17) ----------------------------------------------------


async def test_creating_a_link_without_confirm_says_what_becomes_public() -> None:
    setup = Setup(writes=False)

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.create_link(
            1, expires_in_days=7, file_version="archive", confirm=False
        )

    assert raised.value.message == (
        "Nothing was changed. Confirming would publish the archive file (the original where "
        "there is none) of 'e2e-public-invoice' (id 1) under a public share link that expires "
        "in 7 days; anyone with the URL can download it without logging in."
    )
    assert raised.value.hint == CONFIRM_HINT


async def test_the_refusal_for_a_link_says_when_it_never_expires_and_which_file_goes_out() -> None:
    setup = Setup(writes=False)

    with pytest.raises(
        ConfirmationRequired, match=r"the original file of 'e2e-public-invoice'.*never"
    ):
        _ = await setup.service.create_link(
            1, expires_in_days=None, file_version="original", confirm=False
        )


async def test_creating_a_bundle_without_confirm_names_every_document_and_publishes_nothing() -> (
    None
):
    setup = Setup(writes=False)

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.create_bundle(
            [1, 2], expires_in_days=1, file_version="original", confirm=False
        )

    assert raised.value.message == (
        "Nothing was changed. Confirming would publish the original files of "
        "'e2e-public-invoice' (id 1) and 'e2e-private-note' (id 2) as one ZIP archive under a "
        "public share link that expires in 1 day; anyone with the URL can download it without "
        "logging in."
    )
    assert setup.state.share_bundles == []


async def test_the_confirmation_is_asked_only_for_documents_the_user_can_see() -> None:
    setup = Setup(writes=False)

    with pytest.raises(NotFound, match="No document 99"):
        _ = await setup.service.create_bundle(
            [1, 99], expires_in_days=None, file_version="archive", confirm=False
        )


# --- bundle documents, mail setting ---------------------------------------------------------------


@final
class Counting(FakePaperless):
    """A Paperless that counts the title reads."""

    reads: int = 0

    @override
    async def get_document_titles(self, document_ids: Collection[int]) -> dict[int, str]:
        self.reads += 1
        return await super().get_document_titles(document_ids)


@final
class Blind(FakePaperless):
    """A Paperless that does not let the user read titles."""

    @override
    async def get_document_titles(self, document_ids: Collection[int]) -> dict[int, str]:
        message = "no"
        raise PermissionDenied(message)


async def test_the_titles_of_all_the_listed_bundles_are_read_in_one_call() -> None:
    setup = Setup()
    for documents in ([1], [1, 2]):
        _ = await setup.service.create_bundle(
            documents, expires_in_days=None, file_version="archive", confirm=True
        )
    counting = Counting(setup.state, user=ADMIN[0])
    service = SharingService(counting, counting, counting, web_url=WEB, now=lambda: NOW)

    listing = await service.list_bundles(page=1, page_size=25)

    assert counting.reads == 1
    assert [[d.title for d in b.documents] for b in listing.bundles] == [
        [PUBLIC_TITLE],
        [PUBLIC_TITLE, "e2e-private-note"],
    ]


async def test_an_empty_bundle_listing_reads_no_titles() -> None:
    setup = Setup()
    counting = Counting(setup.state, user=ADMIN[0])
    service = SharingService(counting, counting, counting, web_url=WEB, now=lambda: NOW)

    assert (await service.list_bundles(page=1, page_size=25)).bundles == ()
    assert counting.reads == 0


async def test_a_user_who_may_not_read_titles_still_sees_the_documents_by_id() -> None:
    setup = Setup()
    _ = await setup.service.create_bundle(
        [1], expires_in_days=None, file_version="archive", confirm=True
    )
    blind = Blind(setup.state, user=ADMIN[0])
    service = SharingService(blind, blind, blind, web_url=WEB, now=lambda: NOW)

    [bundle] = (await service.list_bundles(page=1, page_size=25)).bundles

    assert [(d.id, d.title) for d in bundle.documents] == [(1, None)]


async def test_an_email_is_refused_before_the_confirmation_when_paperless_has_no_mail_server() -> (
    None
):
    setup = Setup(writes=False, mail=False)

    with pytest.raises(FeatureUnavailable) as raised:
        _ = await setup.service.email(
            [1],
            recipients=["a@example.com"],
            subject="s",
            message="m",
            use_archive_version=True,
            confirm=False,
        )

    assert not isinstance(raised.value, ConfirmationRequired)


async def test_an_unknown_mail_setting_does_not_stop_the_confirmation() -> None:
    setup = Setup(writes=False, mail=False)
    setup.state.users.append(FakeUser(id=9, username="no-ui", permissions=frozenset()))
    nobody = FakePaperless(setup.state, user="no-ui")
    service = SharingService(nobody, nobody, setup.fake, web_url=WEB, now=lambda: NOW)

    with pytest.raises(ConfirmationRequired):
        _ = await service.email(
            [1],
            recipients=["a@example.com"],
            subject="s",
            message="m",
            use_archive_version=True,
            confirm=False,
        )
