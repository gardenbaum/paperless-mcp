"""The sharing toolset through the in-memory client on the seeded fake (PLMCP-26)."""

from typing import cast

import pytest
from fakes.paperless import FakePaperless, FakeState, seeded
from fastmcp import Client
from harness import serve

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.decision("decision-4"),
    pytest.mark.decision("decision-6"),
    pytest.mark.decision("decision-15"),
]

type Json = dict[str, object]

TOOLS = {
    "list_share_links",
    "create_share_link",
    "delete_share_link",
    "list_share_bundles",
    "create_share_bundle",
    "rebuild_share_bundle",
    "delete_share_bundle",
    "email_documents",
}
READ_ONLY = {"list_share_links", "list_share_bundles"}
WEB = "http://paperless.test"


def fake(
    state: FakeState | None = None, *, writes: bool = True, user: str = "admin"
) -> FakePaperless:
    return FakePaperless(state or seeded(), user=user, allow_writes=writes)


async def call(source: FakePaperless, tool: str, **arguments: object) -> Json:
    async with Client(serve(source, toolsets=["core", "sharing"])) as client:
        result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    return result.structured_content


async def refusal(source: FakePaperless, tool: str, **arguments: object) -> str:
    async with Client(serve(source, toolsets=["core", "sharing"])) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error
    return str(result.content)


async def test_sharing_is_not_in_the_default_profile() -> None:
    async with Client(serve(fake())) as client:
        names = {tool.name for tool in await client.list_tools()}

    assert names.isdisjoint(TOOLS)


async def test_the_sharing_toolset_offers_eight_tools_and_read_only_mode_keeps_the_listings() -> (
    None
):
    async with Client(serve(fake(), toolsets=["sharing"])) as client:
        names = {tool.name for tool in await client.list_tools()}
    async with Client(serve(fake(), toolsets=["sharing"], read_only=True)) as client:
        read_only = {tool.name for tool in await client.list_tools()}

    assert names == TOOLS
    assert read_only == READ_ONLY


async def test_publishing_and_sending_tools_are_marked_open_world() -> None:
    async with Client(serve(fake(), toolsets=["sharing"])) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}

    open_world = {
        name
        for name, tool in tools.items()
        if tool.annotations is not None and tool.annotations.open_world_hint is True
    }
    assert open_world == {
        "create_share_link",
        "create_share_bundle",
        "rebuild_share_bundle",
        "email_documents",
    }
    mail = tools["email_documents"].annotations
    assert mail is not None
    assert (mail.read_only_hint, mail.destructive_hint, mail.idempotent_hint) == (
        False,
        True,
        False,
    )


async def test_a_link_is_created_as_a_full_url_listed_and_deleted() -> None:
    source = fake()

    made = await call(source, "create_share_link", document_id=1, expires_in_days=7, confirm=True)
    listed = await call(source, "list_share_links", document_id=1)

    slug = source.state.share_links[0].slug
    assert made["url"] == f"{WEB}/share/{slug}"
    assert made["web_url"] == f"{WEB}/documents/1/details"
    assert (made["document_title"], made["expired"], made["file_version"]) == (
        "e2e-public-invoice",
        False,
        "archive",
    )
    assert made["expiration"] is not None
    assert listed["count"] == 1
    assert cast("list[Json]", listed["links"])[0]["url"] == made["url"]


async def test_deleting_a_link_without_confirm_is_an_error_that_says_the_effect() -> None:
    state = seeded()
    _ = await call(fake(state), "create_share_link", document_id=1, confirm=True)

    text = await refusal(fake(state, writes=False), "delete_share_link", link_id=1)

    assert "Nothing was changed" in text
    assert "delete share link 1 for the document 'e2e-public-invoice' (id 1)" in text
    assert "confirm=true" in text
    assert len(state.share_links) == 1


async def test_deleting_a_link_with_confirm_removes_it() -> None:
    state = seeded()
    _ = await call(fake(state), "create_share_link", document_id=1, confirm=True)

    output = await call(fake(state), "delete_share_link", link_id=1, confirm=True)

    assert output["id"] == 1
    assert state.share_links == []


async def test_a_bundle_is_created_pending_then_listed_ready_and_rebuilt() -> None:
    source = fake()

    made = await call(
        source,
        "create_share_bundle",
        document_ids=[1, 2],
        expires_in_days=3,
        file_version="original",
        confirm=True,
    )
    listed = await call(source, "list_share_bundles")
    rebuilt = await call(source, "rebuild_share_bundle", bundle_id=1)

    assert (made["status"], made["document_count"], made["file_version"]) == (
        "pending",
        2,
        "original",
    )
    assert str(made["url"]).startswith(f"{WEB}/share/")
    [row] = cast("list[Json]", listed["bundles"])
    assert (row["status"], row["document_count"]) == ("ready", 2)
    assert rebuilt["status"] == "pending"
    assert rebuilt["url"] == made["url"]


async def test_deleting_a_bundle_asks_for_confirm_and_then_deletes() -> None:
    state = seeded()
    _ = await call(fake(state), "create_share_bundle", document_ids=[1], confirm=True)

    text = await refusal(fake(state, writes=False), "delete_share_bundle", bundle_id=1)
    output = await call(fake(state), "delete_share_bundle", bundle_id=1, confirm=True)

    assert "delete share-link bundle 1 of 1 document with its archive" in text
    assert output["id"] == 1
    assert state.share_bundles == []


async def test_an_email_without_confirm_names_recipients_and_documents_and_sends_nothing() -> None:
    state = seeded()
    state.email_enabled = True

    text = await refusal(
        fake(state, writes=False),
        "email_documents",
        document_ids=[1],
        recipients=["a@example.com"],
        subject="Invoice",
        message="Attached.",
    )

    assert "send an e-mail with the archive file (the original where there is none)" in text
    assert "'e2e-public-invoice' (id 1)" in text
    assert "subject 'Invoice', message 'Attached.'" in text
    assert "to a@example.com" in text
    assert "confirm=true" in text
    assert state.outbox == []


async def test_a_confirmed_email_is_sent() -> None:
    state = seeded()
    state.email_enabled = True

    sent = await call(
        fake(state),
        "email_documents",
        document_ids=[1],
        recipients=["a@example.com", "b@example.com"],
        subject="Invoice",
        message="Attached.",
        use_archive_version=False,
        confirm=True,
    )

    assert sent["recipients"] == ["a@example.com", "b@example.com"]
    assert [(m.documents, m.use_archive_version) for m in state.outbox] == [((1,), False)]


async def test_an_email_where_paperless_has_no_mail_server_fails_with_a_hint() -> None:
    text = await refusal(
        fake(),
        "email_documents",
        document_ids=[1],
        recipients=["a@example.com"],
        subject="s",
        message="m",
        confirm=True,
    )

    assert "no mail server set up" in text
    assert "PAPERLESS_EMAIL_HOST" in text


async def test_a_user_without_the_share_permission_gets_a_permission_error() -> None:
    text = await refusal(fake(user="e2e-reader"), "list_share_links")

    assert "may not perform this action" in text


async def test_expiry_outside_the_range_is_refused_by_the_schema() -> None:
    async with Client(serve(fake(), toolsets=["sharing"])) as client:
        result = await client.call_tool(
            "create_share_link", {"document": 1, "expires_in_days": 0}, raise_on_error=False
        )

    assert result.is_error


async def test_the_confirm_parameter_is_the_shared_one_and_off_by_default() -> None:
    async with Client(serve(fake(), toolsets=["sharing"])) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}

    for name in (
        *("delete_share_link", "delete_share_bundle", "email_documents"),
        *("create_share_link", "create_share_bundle"),
    ):
        schema = tools[name].input_schema
        assert cast("dict[str, Json]", schema["properties"])["confirm"]["default"] is False
        assert "confirm" not in cast("list[str]", schema["required"])


async def test_creating_without_confirm_publishes_nothing_and_says_what_becomes_public() -> None:
    state = seeded()

    link = await refusal(fake(state, writes=False), "create_share_link", document_id=1)
    bundle = await refusal(fake(state, writes=False), "create_share_bundle", document_ids=[1, 2])

    for text in (link, bundle):
        assert "Nothing was changed" in text
        assert "anyone with the URL can download it without logging in" in text
        assert "never expires" in text
    assert "'e2e-public-invoice' (id 1) under a public share link" in link
    assert "'e2e-private-note' (id 2) as one ZIP archive" in bundle
    assert (state.share_links, state.share_bundles) == ([], [])


async def test_the_parameters_follow_the_ids_of_the_other_toolsets() -> None:
    async with Client(serve(fake(), toolsets=["sharing"])) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}

    names = {
        name: set(cast("dict[str, Json]", tool.input_schema["properties"]))
        for name, tool in tools.items()
    }
    assert {"document_id", "page", "page_size"} == names["list_share_links"]
    assert "document_id" in names["create_share_link"]
    assert "link_id" in names["delete_share_link"]
    assert "document_ids" in names["create_share_bundle"] | names["email_documents"]
    assert "bundle_id" in names["rebuild_share_bundle"] & names["delete_share_bundle"]


async def test_the_id_lists_and_recipients_are_bounded_by_the_schema() -> None:
    async with Client(serve(fake(), toolsets=["sharing"])) as client:
        bad_bundles: list[Json] = [
            {"document_ids": []},
            {"document_ids": [0]},
            {"document_ids": list(range(1, 52))},
        ]
        for arguments in bad_bundles:
            result = await client.call_tool("create_share_bundle", arguments, raise_on_error=False)
            assert result.is_error
        mail = {"document_ids": [1], "subject": "s", "message": "m"}
        for recipients in ([], [f"u{n}@example.com" for n in range(21)]):
            result = await client.call_tool(
                "email_documents", {**mail, "recipients": recipients}, raise_on_error=False
            )
            assert result.is_error
