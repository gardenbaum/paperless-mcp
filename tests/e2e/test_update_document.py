"""update_document through the assembled server on Paperless 3.4.2, on documents of its own."""

import uuid
from typing import TYPE_CHECKING, cast

import pytest
from fastmcp import Client
from harness import paperless_settings, server_settings

from e2e.helpers import consume, obj, parse, pdf, results
from paperless_mcp.server import create_server

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastmcp import FastMCP

    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]

type Json = dict[str, object]


@pytest.fixture
def document(stack: Stack) -> Iterator[int]:
    """A new document of the admin (deleted again, also when the test fails)."""
    title = f"plmcp16-{uuid.uuid4().hex}"
    _, created = consume(stack.admin, title, pdf(title))
    assert created is not None
    yield created
    _ = stack.admin.delete(f"/api/documents/{created}/")


@pytest.fixture
def other_document(stack: Stack) -> Iterator[int]:
    """A second document of its own, to link to (never a seeded one: links are mirrored)."""
    title = f"plmcp16-{uuid.uuid4().hex}"
    _, created = consume(stack.admin, title, pdf(title))
    assert created is not None
    yield created
    _ = stack.admin.delete(f"/api/documents/{created}/")


@pytest.fixture
def scratch_tags(stack: Stack) -> Iterator[str]:
    """A tag name unique to the test; a tag of that name is deleted afterwards."""
    name = f"plmcp16-tag-{uuid.uuid4().hex}"
    yield name
    for tag in results(stack.admin.get("/api/tags/", params={"name__iexact": name})):
        _ = stack.admin.delete(f"/api/tags/{tag['id']}/")


def server(stack: Stack, token: str, *, read_only: bool = False) -> FastMCP:
    return create_server(
        paperless_settings(stack.base_url, token), server_settings(read_only=read_only)
    )


async def update(stack: Stack, token: str, document: int, **arguments: object) -> Json:
    async with Client(server(stack, token)) as client:
        result = await client.call_tool("update_document", {"document_id": document, **arguments})
    assert result.structured_content is not None
    return result.structured_content


def values(detail: Json) -> dict[str, object]:
    return {str(f["name"]): f["value"] for f in cast("list[Json]", detail["custom_fields"])}


def tag_names(detail: Json) -> set[str]:
    return {str(t["name"]) for t in cast("list[Json]", detail["tags"])}


async def test_setting_one_custom_field_keeps_the_others_on_live_paperless(
    stack: Stack, document: int
) -> None:
    first = await update(
        stack,
        stack.admin_token,
        document,
        custom_fields={"e2e-string": "one", "e2e-integer": 1, "e2e-boolean": True},
    )
    second = await update(stack, stack.admin_token, document, custom_fields={"e2e-string": "two"})
    third = await update(stack, stack.admin_token, document, remove_custom_fields=["e2e-integer"])

    assert values(first) == {"e2e-string": "one", "e2e-integer": 1, "e2e-boolean": True}
    assert values(second) == {"e2e-string": "two", "e2e-integer": 1, "e2e-boolean": True}
    assert values(third) == {"e2e-string": "two", "e2e-boolean": True}
    raw = obj(
        parse(stack.admin.get(f"/api/documents/{document}/", params={"fields": "custom_fields"}))
    )
    assert len(cast("list[object]", raw["custom_fields"])) == 2


async def test_every_data_type_is_written_in_the_form_paperless_stores(
    stack: Stack, document: int, other_document: int
) -> None:
    other_title = str(
        obj(
            parse(stack.admin.get(f"/api/documents/{other_document}/", params={"fields": "title"}))
        )["title"]
    )
    detail = await update(
        stack,
        stack.admin_token,
        document,
        custom_fields={
            "e2e-string": "s",
            "e2e-url": "https://example.com/x",
            "e2e-date": "2026-03-04",
            "e2e-boolean": False,
            "e2e-integer": 3,
            "e2e-float": 1.5,
            "e2e-monetary": "usd 12.5",
            "e2e-documentlink": [other_document],
            "e2e-longtext": "a\nb",
            "e2e-select": "beta",
        },
    )

    assert values(detail) == {
        "e2e-string": "s",
        "e2e-url": "https://example.com/x",
        "e2e-date": "2026-03-04",
        "e2e-boolean": False,
        "e2e-integer": 3,
        "e2e-float": 1.5,
        "e2e-monetary": "USD 12.50",
        "e2e-documentlink": [{"id": other_document, "name": other_title}],
        "e2e-longtext": "a\nb",
        "e2e-select": "beta",
    }
    stored = results(
        stack.admin.get("/api/documents/", params={"id": document, "fields": "custom_fields"})
    )
    monetary = [
        f["value"]
        for f in cast("list[Json]", stored[0]["custom_fields"])
        if f["field"] == stack.seeded.custom_fields["monetary"]
    ]
    assert monetary == ["USD12.50"]


async def test_names_tags_and_scalars_change_in_one_call(stack: Stack, document: int) -> None:
    detail = await update(
        stack,
        stack.admin_token,
        document,
        title="plmcp16 renamed",
        correspondent="E2E-ACME",
        document_type="e2e-invoice",
        storage_path="e2e-archive",
        tags_set=["e2e-finance", "e2e-inbox"],
        created="2025-01-02",
    )
    again = await update(stack, stack.admin_token, document, tags_remove=["e2e-inbox"])
    cleared = await update(stack, stack.admin_token, document, clear=["correspondent"])

    assert detail["title"] == "plmcp16 renamed"
    assert detail["created"] == "2025-01-02"
    assert cast("Json", detail["correspondent"])["name"] == "e2e-acme"
    assert cast("Json", detail["document_type"])["name"] == "e2e-invoice"
    assert cast("Json", detail["storage_path"])["name"] == "e2e-archive"
    assert tag_names(detail) == {"e2e-finance", "e2e-inbox"}
    assert tag_names(again) == {"e2e-finance"}
    assert cleared["correspondent"] is None
    assert cleared["title"] == "plmcp16 renamed"


async def test_unknown_tags_fail_with_a_hint_unless_they_may_be_created(
    stack: Stack, document: int, scratch_tags: str
) -> None:
    async with Client(server(stack, stack.admin_token)) as client:
        refused = await client.call_tool(
            "update_document",
            {"document_id": document, "tags_add": [scratch_tags]},
            raise_on_error=False,
        )
        assert refused.is_error
        assert "create_missing_tags" in str(refused.content)
        assert results(stack.admin.get("/api/tags/", params={"name__iexact": scratch_tags})) == []

        created = await client.call_tool(
            "update_document",
            {"document_id": document, "tags_add": [scratch_tags], "create_missing_tags": True},
        )

    assert created.structured_content is not None
    assert scratch_tags in tag_names(created.structured_content)
    assert len(results(stack.admin.get("/api/tags/", params={"name__iexact": scratch_tags}))) == 1


async def test_a_bad_value_changes_nothing_and_a_restricted_user_is_refused(
    stack: Stack, document: int
) -> None:
    async with Client(server(stack, stack.admin_token)) as client:
        bad = await client.call_tool(
            "update_document",
            {"document_id": document, "title": "never", "custom_fields": {"e2e-integer": "x"}},
            raise_on_error=False,
        )
    async with Client(server(stack, stack.reader_token)) as client:
        denied = await client.call_tool(
            "update_document",
            {"document_id": stack.seeded.public_document, "title": "never"},
            raise_on_error=False,
        )

    assert bad.is_error
    assert "e2e-integer" in str(bad.content)
    assert denied.is_error
    assert "may not perform this action" in str(denied.content)
    title = obj(parse(stack.admin.get(f"/api/documents/{document}/", params={"fields": "title"})))
    assert str(title["title"]).startswith("plmcp16-")
    public = obj(
        parse(
            stack.admin.get(
                f"/api/documents/{stack.seeded.public_document}/", params={"fields": "title"}
            )
        )
    )
    assert public["title"] == "e2e-public-invoice"


async def test_read_only_mode_does_not_offer_the_tool(stack: Stack) -> None:
    async with Client(server(stack, stack.admin_token, read_only=True)) as client:
        names = {tool.name for tool in await client.list_tools()}

    assert "update_document" not in names
    assert "get_document" in names


async def test_the_tag_hierarchy_is_closed_never_silently_dropped_and_idempotent(
    stack: Stack, document: int
) -> None:
    first = await update(stack, stack.admin_token, document, tags_set=["e2e-invoices", "e2e-inbox"])
    again = await update(stack, stack.admin_token, document, tags_set=["e2e-invoices", "e2e-inbox"])
    parent_only = await update(stack, stack.admin_token, document, tags_set=["e2e-finance"])
    child_again = await update(stack, stack.admin_token, document, tags_add=["e2e-invoices"])

    assert tag_names(first) == {"e2e-finance", "e2e-invoices", "e2e-inbox"}
    assert [t["name"] for t in cast("list[Json]", first["implicit_tags"])] == ["e2e-finance"]
    assert again["modified"] == first["modified"]
    assert again["implicit_tags"] == []
    assert tag_names(parent_only) == {"e2e-finance"}
    assert tag_names(child_again) == {"e2e-finance", "e2e-invoices"}


async def test_a_parent_tag_whose_children_stay_cannot_be_removed(
    stack: Stack, document: int
) -> None:
    _ = await update(stack, stack.admin_token, document, tags_set=["e2e-invoices"])
    async with Client(server(stack, stack.admin_token)) as client:
        refused = await client.call_tool(
            "update_document",
            {"document_id": document, "tags_remove": ["e2e-finance"]},
            raise_on_error=False,
        )
    both = await update(
        stack, stack.admin_token, document, tags_remove=["e2e-finance", "e2e-invoices"]
    )

    assert refused.is_error
    assert "'e2e-invoices'" in str(refused.content)
    assert tag_names(both) == set()


async def test_links_are_mirrored_and_a_hidden_document_cannot_be_linked(
    stack: Stack, document: int, other_document: int
) -> None:
    _ = await update(
        stack, stack.admin_token, document, custom_fields={"e2e-documentlink": [other_document]}
    )
    mirrored = obj(
        parse(
            stack.admin.get(f"/api/documents/{other_document}/", params={"fields": "custom_fields"})
        )
    )
    async with Client(server(stack, stack.reader_token)) as client:
        hidden = await client.call_tool(
            "update_document",
            {
                "document_id": stack.seeded.public_document,
                "custom_fields": {"e2e-documentlink": [document]},
            },
            raise_on_error=False,
        )

    assert mirrored["custom_fields"] == [
        {"field": stack.seeded.custom_fields["documentlink"], "value": [document]}
    ]
    assert hidden.is_error


@pytest.mark.decision("decision-12")
async def test_an_owner_change_needs_confirm_and_then_hands_the_document_over(
    stack: Stack, document: int
) -> None:
    async with Client(server(stack, stack.admin_token)) as client:
        refused = await client.call_tool(
            "update_document", {"document_id": document, "clear": ["owner"]}, raise_on_error=False
        )
        done = await client.call_tool(
            "update_document", {"document_id": document, "clear": ["owner"], "confirm": True}
        )

    assert refused.is_error
    assert "confirm=true" in str(refused.content)
    assert done.structured_content is not None
    assert done.structured_content["owner"] is None
