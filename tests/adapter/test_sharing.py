"""PaperlessSharingReader and PaperlessSharingWriter on the Paperless 3.4.2 wire format."""

import json
import os
import time
from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING, cast

import httpx2
import pytest

from paperless_mcp.domain.errors import (
    DeliveryUnknown,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    UpstreamUnavailable,
    Validation,
)
from paperless_mcp.domain.sharing import ShareBundle, ShareLink
from paperless_mcp.paperless.pagination import MAX_ITEMS
from paperless_mcp.paperless.sharing import (
    EMAIL_TIMEOUT,
    PaperlessSharingReader,
    PaperlessSharingWriter,
)

if TYPE_CHECKING:
    from adapter.conftest import ClientFactory

pytestmark = pytest.mark.anyio

type Json = dict[str, object]

SETTINGS: Json = {
    "update_checking": {"backend_setting": "default"},
    "trash_delay": 30,
    "version": "3.4.2",
    "app_title": None,
    "app_logo": None,
    "auditlog_enabled": True,
    "remote_ocr": {"configured": False, "mode": "always"},
    "email_enabled": False,
    "ai_enabled": False,
}
UI_SETTINGS: Json = {
    "user": {"id": 2, "username": "admin", "is_staff": True, "is_superuser": True, "groups": []},
    "settings": SETTINGS,
    "permissions": ["view_documenttype"],
}

LINK_WIRE: Json = {
    "id": 4,
    "created": "2026-11-13T18:36:02.893751Z",
    "expiration": "2030-01-01T00:00:00Z",
    "slug": "WNb9fg74ahJDaGlsarMTfX5VCNUK0wPYlfNGFdwlH9TtIRV877",
    "document": 12,
    "document_title": "Invoice",
    "file_version": "original",
}
LINK = ShareLink(
    id=4,
    document_id=12,
    document_title="Invoice",
    slug="WNb9fg74ahJDaGlsarMTfX5VCNUK0wPYlfNGFdwlH9TtIRV877",
    created=datetime(2026, 11, 13, 18, 36, 2, 893751, tzinfo=UTC),
    expiration=datetime(2030, 1, 1, tzinfo=UTC),
    file_version="original",
)
BUNDLE_WIRE: Json = {
    "id": 3,
    "created": "2026-11-13T18:36:03.624840Z",
    "expiration": None,
    "slug": "I1bPW8ZmvThP598dj6mKbDdt3CEXACdtJgSOH3erLrGNFjt6ZA",
    "file_version": "archive",
    "status": "ready",
    "size_bytes": 12151,
    "last_error": None,
    "built_at": "2026-11-13T18:36:03.950005Z",
    "documents": [12, 13],
    "document_count": 2,
}
BUNDLE = ShareBundle(
    id=3,
    slug="I1bPW8ZmvThP598dj6mKbDdt3CEXACdtJgSOH3erLrGNFjt6ZA",
    created=datetime(2026, 11, 13, 18, 36, 3, 624840, tzinfo=UTC),
    expiration=None,
    file_version="archive",
    status="ready",
    size_bytes=12151,
    last_error=None,
    built_at=datetime(2026, 11, 13, 18, 36, 3, 950005, tzinfo=UTC),
    document_ids=(12, 13),
)


class Recording:
    """Answers each request with the next scripted response and records method, path, body."""

    def __init__(self, *answers: httpx2.Response) -> None:
        self.answers: list[httpx2.Response] = list(answers)
        self.sent: list[tuple[str, str, Json | None, dict[str, str]]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = cast("Json", json.loads(request.content)) if request.content else None
        self.sent.append((request.method, request.url.path, body, dict(request.url.params)))
        return self.answers.pop(0)


def page(*rows: Json, next_link: str | None = None, count: int | None = None) -> httpx2.Response:
    body = {
        "count": len(rows) if count is None else count,
        "next": next_link,
        "previous": None,
        "results": list(rows),
    }
    return httpx2.Response(200, json=body)


async def test_the_share_links_are_listed(make_client: ClientFactory) -> None:
    server = Recording(page(LINK_WIRE))

    found = await PaperlessSharingReader(make_client(server)).list_share_links()

    assert found == (LINK,)
    assert server.sent == [("GET", "/api/share_links/", None, {"page": "1", "page_size": "100"})]


async def test_every_page_of_links_is_read(make_client: ClientFactory) -> None:
    link = "http://paperless.test/api/share_links/?page=2&page_size=100"
    second = {**LINK_WIRE, "id": 5}
    server = Recording(page(LINK_WIRE, next_link=link, count=2), page(second, count=2))

    found = await PaperlessSharingReader(make_client(server)).list_share_links()

    assert [item.id for item in found] == [4, 5]


async def test_more_links_than_the_cap_fail_instead_of_showing_a_part(
    make_client: ClientFactory,
) -> None:
    server = Recording(page(LINK_WIRE, count=MAX_ITEMS + 1))

    with pytest.raises(PaperlessMcpError, match=f"more than {MAX_ITEMS} share links"):
        _ = await PaperlessSharingReader(make_client(server)).list_share_links()


async def test_a_user_without_the_view_permission_is_denied(make_client: ClientFactory) -> None:
    server = Recording(httpx2.Response(403, json={"detail": "no"}))

    with pytest.raises(PermissionDenied):
        _ = await PaperlessSharingReader(make_client(server)).list_share_links()


async def test_one_link_is_read_by_id(make_client: ClientFactory) -> None:
    server = Recording(httpx2.Response(200, json=LINK_WIRE))

    found = await PaperlessSharingReader(make_client(server)).get_share_link(4)

    assert found == LINK
    assert server.sent == [("GET", "/api/share_links/4/", None, {})]


async def test_a_missing_link_is_not_found_with_a_hint(make_client: ClientFactory) -> None:
    server = Recording(httpx2.Response(404, json={"detail": "No ShareLink matches."}))

    with pytest.raises(NotFound, match="No share link 9") as raised:
        _ = await PaperlessSharingReader(make_client(server)).get_share_link(9)

    assert raised.value.hint == "Call list_share_links to see the links you can use."


async def test_a_link_row_without_a_document_has_no_document(make_client: ClientFactory) -> None:
    server = Recording(page({k: v for k, v in LINK_WIRE.items() if k != "document"}))

    [found] = await PaperlessSharingReader(make_client(server)).list_share_links()

    assert found.document_id is None


async def test_the_bundles_are_listed_with_their_build_state(make_client: ClientFactory) -> None:
    failed = {**BUNDLE_WIRE, "id": 4, "status": "failed", "last_error": {"message": "disk full"}}
    server = Recording(page(BUNDLE_WIRE, failed))

    found = await PaperlessSharingReader(make_client(server)).list_share_bundles()

    assert found[0] == BUNDLE
    assert (found[1].status, found[1].last_error) == ("failed", "disk full")
    assert server.sent == [
        ("GET", "/api/share_link_bundles/", None, {"page": "1", "page_size": "100"})
    ]


@pytest.mark.parametrize(
    ("error", "shown"),
    [
        pytest.param("plain text", "plain text", id="text"),
        pytest.param({"error": "x"}, '{"error": "x"}', id="object without message"),
        pytest.param({"message": 5}, '{"message": 5}', id="message that is no text"),
        pytest.param("x" * 500, "x" * 199 + "…", id="long"),
        pytest.param("x" * 201, "x" * 199 + "…", id="one too long"),
    ],
)
async def test_a_build_error_is_shown_as_one_bounded_line(
    make_client: ClientFactory, error: object, shown: str
) -> None:
    server = Recording(
        httpx2.Response(200, json={**BUNDLE_WIRE, "status": "failed", "last_error": error})
    )

    found = await PaperlessSharingReader(make_client(server)).get_share_bundle(3)

    assert found.last_error == shown


async def test_more_bundles_than_the_cap_fail(make_client: ClientFactory) -> None:
    server = Recording(page(BUNDLE_WIRE, count=MAX_ITEMS + 1))

    with pytest.raises(PaperlessMcpError) as raised:
        _ = await PaperlessSharingReader(make_client(server)).list_share_bundles()

    assert raised.value.message == (
        f"Paperless has more than {MAX_ITEMS} share-link bundles; this server reads up to that."
    )


async def test_one_bundle_is_read_by_id(make_client: ClientFactory) -> None:
    server = Recording(httpx2.Response(200, json=BUNDLE_WIRE))

    assert await PaperlessSharingReader(make_client(server)).get_share_bundle(3) == BUNDLE
    assert server.sent == [("GET", "/api/share_link_bundles/3/", None, {})]


async def test_a_missing_bundle_is_not_found_with_a_hint(make_client: ClientFactory) -> None:
    server = Recording(httpx2.Response(404, json={"detail": "No ShareLinkBundle matches."}))

    with pytest.raises(NotFound, match="No share-link bundle 9") as raised:
        _ = await PaperlessSharingReader(make_client(server)).get_share_bundle(9)

    assert raised.value.hint == "Call list_share_bundles to see the bundles you can use."


async def test_a_created_link_sends_the_document_expiry_and_file(
    make_client: ClientFactory,
) -> None:
    server = Recording(httpx2.Response(201, json=LINK_WIRE))
    expiry = datetime(2030, 1, 1, tzinfo=UTC)

    made = await PaperlessSharingWriter(make_client(server)).create_share_link(
        12, expiration=expiry, file_version="original"
    )

    assert made == LINK
    assert server.sent == [
        (
            "POST",
            "/api/share_links/",
            {"document": 12, "expiration": "2030-01-01T00:00:00Z", "file_version": "original"},
            {},
        )
    ]


async def test_a_link_without_expiry_sends_none(make_client: ClientFactory) -> None:
    server = Recording(httpx2.Response(201, json={**LINK_WIRE, "expiration": None}))

    made = await PaperlessSharingWriter(make_client(server)).create_share_link(
        12, expiration=None, file_version="archive"
    )

    assert made.expiration is None
    assert server.sent[0][2] == {"document": 12, "expiration": None, "file_version": "archive"}


async def test_a_link_for_an_unknown_document_is_a_validation_error(
    make_client: ClientFactory,
) -> None:
    server = Recording(
        httpx2.Response(400, json={"document": ['Invalid pk "99999" - object does not exist.']})
    )

    with pytest.raises(Validation, match="document"):
        _ = await PaperlessSharingWriter(make_client(server)).create_share_link(
            99999, expiration=None, file_version="archive"
        )


async def test_a_link_is_deleted_without_retry_and_a_missing_one_is_not_found(
    make_client: ClientFactory,
) -> None:
    server = Recording(httpx2.Response(204), httpx2.Response(404, json={"detail": "Not found."}))
    writer = PaperlessSharingWriter(make_client(server))

    await writer.delete_share_link(4)
    with pytest.raises(NotFound, match="No share link 4"):
        await writer.delete_share_link(4)

    assert [(m, p) for m, p, _, _ in server.sent] == [("DELETE", "/api/share_links/4/")] * 2


async def test_a_created_bundle_sends_the_documents_expiry_days_and_file(
    make_client: ClientFactory,
) -> None:
    server = Recording(
        httpx2.Response(201, json={**BUNDLE_WIRE, "status": "pending", "size_bytes": None})
    )

    made = await PaperlessSharingWriter(make_client(server)).create_share_bundle(
        [13, 12], expiration_days=3, file_version="original"
    )

    assert made.status == "pending"
    assert server.sent == [
        (
            "POST",
            "/api/share_link_bundles/",
            {"document_ids": [13, 12], "expiration_days": 3, "file_version": "original"},
            {},
        )
    ]


async def test_a_bundle_without_expiry_sends_no_days(make_client: ClientFactory) -> None:
    server = Recording(httpx2.Response(201, json=BUNDLE_WIRE))

    _ = await PaperlessSharingWriter(make_client(server)).create_share_bundle(
        [12], expiration_days=None, file_version="archive"
    )

    assert server.sent[0][2] == {"document_ids": [12], "file_version": "archive"}


async def test_a_bundle_with_an_unknown_document_is_a_validation_error(
    make_client: ClientFactory,
) -> None:
    server = Recording(httpx2.Response(400, json={"document_ids": "Documents not found: 99999"}))

    with pytest.raises(Validation, match="Documents not found: 99999"):
        _ = await PaperlessSharingWriter(make_client(server)).create_share_bundle(
            [99999], expiration_days=None, file_version="archive"
        )


async def test_a_bundle_is_rebuilt_with_its_documents_and_comes_back_pending(
    make_client: ClientFactory,
) -> None:
    server = Recording(
        httpx2.Response(200, json={**BUNDLE_WIRE, "status": "failed"}),
        httpx2.Response(200, json={**BUNDLE_WIRE, "status": "pending", "size_bytes": None}),
    )

    rebuilt = await PaperlessSharingWriter(make_client(server)).rebuild_share_bundle(3)

    assert rebuilt.status == "pending"
    # The schema requires a body, though 3.4.2 ignores it: the bundle's own documents are sent.
    assert server.sent == [
        ("GET", "/api/share_link_bundles/3/", None, {}),
        ("POST", "/api/share_link_bundles/3/rebuild/", {"document_ids": [12, 13]}, {}),
    ]


async def test_rebuilding_a_missing_bundle_is_not_found(make_client: ClientFactory) -> None:
    server = Recording(httpx2.Response(404, json={"detail": "Not found."}))

    with pytest.raises(NotFound, match="No share-link bundle 3"):
        _ = await PaperlessSharingWriter(make_client(server)).rebuild_share_bundle(3)


async def test_a_refused_rebuild_carries_the_reason(make_client: ClientFactory) -> None:
    server = Recording(
        httpx2.Response(200, json=BUNDLE_WIRE),
        httpx2.Response(400, json={"detail": "Bundle is already building."}),
    )

    with pytest.raises(Validation, match="already building"):
        _ = await PaperlessSharingWriter(make_client(server)).rebuild_share_bundle(3)


async def test_a_bundle_is_deleted_and_a_missing_one_is_not_found(
    make_client: ClientFactory,
) -> None:
    server = Recording(httpx2.Response(204), httpx2.Response(404, json={"detail": "Not found."}))
    writer = PaperlessSharingWriter(make_client(server))

    await writer.delete_share_bundle(3)
    with pytest.raises(NotFound, match="No share-link bundle 3"):
        await writer.delete_share_bundle(3)


async def test_an_email_sends_the_documents_addresses_text_and_file_choice(
    make_client: ClientFactory,
) -> None:
    server = Recording(httpx2.Response(200, json={"message": "Email sent"}))

    await PaperlessSharingWriter(make_client(server)).email_documents(
        [12, 13],
        addresses=["a@example.com", "b@example.com"],
        subject="Invoices",
        message="Attached.",
        use_archive_version=False,
    )

    assert server.sent == [
        (
            "POST",
            "/api/documents/email/",
            {
                "documents": [12, 13],
                "addresses": "a@example.com,b@example.com",
                "subject": "Invoices",
                "message": "Attached.",
                "use_archive_version": False,
            },
            {},
        )
    ]


async def test_an_email_is_never_sent_twice_on_a_transient_failure(
    make_client: ClientFactory,
) -> None:
    server = Recording(httpx2.Response(502, text="bad gateway"), httpx2.Response(200, json={}))

    with pytest.raises(DeliveryUnknown):
        await PaperlessSharingWriter(make_client(server)).email_documents(
            [12], addresses=["a@example.com"], subject="s", message="m", use_archive_version=True
        )

    assert len(server.sent) == 1


@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_a_failure_while_sending_says_it_is_unknown_whether_it_was_sent(
    make_client: ClientFactory, status: int
) -> None:
    server = Recording(httpx2.Response(status, json="Error emailing documents, check logs."))

    with pytest.raises(DeliveryUnknown) as raised:
        await PaperlessSharingWriter(make_client(server)).email_documents(
            [12], addresses=["a@example.com"], subject="s", message="m", use_archive_version=True
        )

    assert raised.value.describe() == (
        "Paperless failed while sending the e-mail; whether any recipient got it is unknown. "
        "Hint: Check the Paperless log and ask the user before sending again: Paperless sends "
        "synchronously, so a repeat can send it twice."
    )


async def test_a_throttled_email_stays_a_temporary_failure(make_client: ClientFactory) -> None:
    server = Recording(httpx2.Response(429, json={"detail": "Request was throttled."}))

    with pytest.raises(UpstreamUnavailable) as raised:
        await PaperlessSharingWriter(make_client(server)).email_documents(
            [12], addresses=["a@example.com"], subject="s", message="m", use_archive_version=True
        )

    assert not isinstance(raised.value, DeliveryUnknown)


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(httpx2.ReadTimeout, id="timeout"),
        pytest.param(httpx2.ConnectError, id="unreachable"),
    ],
)
async def test_an_email_without_an_answer_says_it_is_unknown_whether_it_was_sent(
    make_client: ClientFactory, failure: type[httpx2.TransportError]
) -> None:
    def failing(request: httpx2.Request) -> httpx2.Response:
        message = "no answer"
        raise failure(message, request=request)

    with pytest.raises(DeliveryUnknown) as raised:
        await PaperlessSharingWriter(make_client(failing)).email_documents(
            [12], addresses=["a@example.com"], subject="s", message="m", use_archive_version=True
        )

    assert raised.value.describe() == (
        "Paperless did not answer; whether the e-mail was sent is unknown. "
        "Hint: Check with the user and the Paperless log before sending again: Paperless sends "
        "synchronously, so a repeat can send it twice."
    )


async def test_an_email_gets_a_longer_timeout_than_ordinary_requests(
    make_client: ClientFactory,
) -> None:
    seen: list[float] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(cast("float", request.extensions["timeout"]["read"]))
        return httpx2.Response(200, json={"message": "ok"})

    await PaperlessSharingWriter(make_client(handler)).email_documents(
        [12], addresses=["a@example.com"], subject="s", message="m", use_archive_version=True
    )

    assert seen == [EMAIL_TIMEOUT]


@pytest.mark.parametrize("flag", [True, False, None])
async def test_the_mail_setting_is_read_from_the_ui_settings(
    make_client: ClientFactory, *, flag: bool | None
) -> None:
    settings = {k: v for k, v in SETTINGS.items() if k != "email_enabled"}
    settings.update({} if flag is None else {"email_enabled": flag})
    server = Recording(httpx2.Response(200, json={**UI_SETTINGS, "settings": settings}))

    found = await PaperlessSharingReader(make_client(server)).get_email_enabled()

    assert found is flag
    assert server.sent == [("GET", "/api/ui_settings/", None, {})]


async def test_a_user_who_may_not_view_the_ui_settings_has_an_unknown_mail_setting(
    make_client: ClientFactory,
) -> None:
    server = Recording(httpx2.Response(403, json={"detail": "no"}))

    assert await PaperlessSharingReader(make_client(server)).get_email_enabled() is None


@pytest.mark.parametrize(
    "body",
    [
        {"addresses": ["Invalid email address: nope"]},
        {"documents": ["At least one document is required"]},
        {"subject": ["This field may not be blank."]},
    ],
    ids=["address", "documents", "subject"],
)
async def test_an_email_paperless_rejects_is_a_validation_error(
    make_client: ClientFactory, body: Json
) -> None:
    server = Recording(httpx2.Response(400, json=body))

    with pytest.raises(Validation):
        await PaperlessSharingWriter(make_client(server)).email_documents(
            [12], addresses=["nope"], subject="s", message="m", use_archive_version=True
        )


async def test_a_delete_that_hits_a_transient_error_is_not_repeated(
    make_client: ClientFactory,
) -> None:
    for delete in ("delete_share_link", "delete_share_bundle"):
        server = Recording(httpx2.Response(503, text="busy"), httpx2.Response(204))
        writer = PaperlessSharingWriter(make_client(server))

        with pytest.raises(PaperlessMcpError):
            await getattr(writer, delete)(4)

        assert len(server.sent) == 1


async def test_a_rebuild_that_hits_a_transient_error_is_not_repeated(
    make_client: ClientFactory,
) -> None:
    server = Recording(
        httpx2.Response(200, json=BUNDLE_WIRE),
        httpx2.Response(503, text="busy"),
        httpx2.Response(200, json=BUNDLE_WIRE),
    )

    with pytest.raises(PaperlessMcpError):
        _ = await PaperlessSharingWriter(make_client(server)).rebuild_share_bundle(3)

    assert [(m, p) for m, p, _, _ in server.sent] == [
        ("GET", "/api/share_link_bundles/3/"),
        ("POST", "/api/share_link_bundles/3/rebuild/"),
    ]


async def test_a_link_expiry_in_another_zone_is_sent_in_utc(make_client: ClientFactory) -> None:
    server = Recording(httpx2.Response(201, json=LINK_WIRE))
    zone = timezone(timedelta(hours=2))

    _ = await PaperlessSharingWriter(make_client(server)).create_share_link(
        12, expiration=datetime(2030, 1, 1, 2, 0, tzinfo=zone), file_version="archive"
    )

    assert server.sent[0][2] is not None
    assert server.sent[0][2]["expiration"] == "2030-01-01T00:00:00Z"


async def test_a_link_expiry_is_sent_in_utc_whatever_the_local_zone_is(
    make_client: ClientFactory,
) -> None:
    server = Recording(httpx2.Response(201, json=LINK_WIRE))
    before = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Tokyo"
    time.tzset()
    try:
        _ = await PaperlessSharingWriter(make_client(server)).create_share_link(
            12, expiration=datetime(2030, 1, 1, tzinfo=UTC), file_version="archive"
        )
    finally:
        if before is None:
            del os.environ["TZ"]
        else:
            os.environ["TZ"] = before
        time.tzset()

    assert server.sent[0][2] is not None
    assert server.sent[0][2]["expiration"] == "2030-01-01T00:00:00Z"


async def test_a_bundle_keeps_its_expiry_and_a_long_error_is_cut_at_the_limit(
    make_client: ClientFactory,
) -> None:
    expiring = {**BUNDLE_WIRE, "expiration": "2030-01-01T00:00:00Z"}
    exact = {**BUNDLE_WIRE, "id": 5, "status": "failed", "last_error": "y" * 200}
    server = Recording(httpx2.Response(200, json=expiring), httpx2.Response(200, json=exact))
    reader = PaperlessSharingReader(make_client(server))

    assert (await reader.get_share_bundle(3)).expiration == datetime(2030, 1, 1, tzinfo=UTC)
    assert (await reader.get_share_bundle(5)).last_error == "y" * 200


async def test_an_odd_build_error_is_shown_as_json_text(make_client: ClientFactory) -> None:
    server = Recording(
        httpx2.Response(200, json={**BUNDLE_WIRE, "status": "failed", "last_error": [1, "a"]})
    )

    found = await PaperlessSharingReader(make_client(server)).get_share_bundle(3)

    assert found.last_error == '[1, "a"]'
