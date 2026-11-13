"""The bulk toolset through the assembled server on Paperless 3.4.2, on objects of its own.

Documents and taxonomy are created by the tests and removed again (documents with their trash
entries), so nothing is left behind and no seeded object is changed.
"""

import uuid
from typing import TYPE_CHECKING, cast

import pytest
from contract.conftest import delete_and_empty_trash, live_in_trash
from fastmcp import Client
from harness import paperless_settings, server_settings

from e2e.helpers import consume, num, obj, parse, pdf, results, seq
from paperless_mcp.server import create_server

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastmcp import FastMCP

    from e2e.helpers import Json, Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]

PREFIX = "plmcp22-"
PATHS = ("tags", "correspondents", "document_types", "storage_paths", "custom_fields")


@pytest.fixture
def documents(stack: Stack) -> Iterator[list[int]]:
    """Two new documents of the admin; deleted and removed from the trash again."""
    created: list[int] = []
    for number in (1, 2):
        title = f"{PREFIX}{number}-{uuid.uuid4().hex}"
        _, document = consume(stack.admin, title, pdf(title))
        assert document is not None
        created.append(document)
    yield created
    delete_and_empty_trash(stack, created)


@pytest.fixture
def scratch(stack: Stack) -> Iterator[str]:
    """A unique name prefix; every object whose name starts with it is deleted afterwards."""
    prefix = f"{PREFIX}{uuid.uuid4().hex[:8]}-"
    yield prefix
    for kind in PATHS:
        found = stack.admin.get(f"/api/{kind}/", params={"name__istartswith": prefix})
        for row in results(found):
            _ = stack.admin.delete(f"/api/{kind}/{row['id']}/")


def server(stack: Stack, token: str, *, read_only: bool = False) -> FastMCP:
    settings = server_settings(toolsets=["core", "bulk"], read_only=read_only)
    return create_server(paperless_settings(stack.base_url, token), settings)


async def run(stack: Stack, token: str, tool: str, **arguments: object) -> Json:
    async with Client(server(stack, token)) as client:
        result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    return result.structured_content


async def refused(stack: Stack, token: str, tool: str, **arguments: object) -> str:
    async with Client(server(stack, token)) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error
    return str(result.content)


def make(stack: Stack, kind: str, body: Json) -> int:
    return num(obj(parse(stack.admin.post(f"/api/{kind}/", json=body).raise_for_status()))["id"])


def raw(stack: Stack, document: int) -> Json:
    return obj(parse(stack.admin.get(f"/api/documents/{document}/").raise_for_status()))


def tags_of(stack: Stack, document: int) -> set[int]:
    return {num(tag) for tag in seq(raw(stack, document)["tags"])}


def fields_of(stack: Stack, document: int) -> dict[int, object]:
    values = (obj(v) for v in seq(raw(stack, document)["custom_fields"]))
    return {num(v["field"]): v["value"] for v in values}


async def test_tags_are_previewed_confirmed_and_follow_the_hierarchy(
    stack: Stack, documents: list[int], scratch: str
) -> None:
    parent = make(stack, "tags", {"name": f"{scratch}parent"})
    child = make(stack, "tags", {"name": f"{scratch}child", "parent": parent})
    before = [tags_of(stack, d) for d in documents]
    add = {"op": "add_tags", "tags": [f"{scratch}child"]}

    preview = await run(
        stack,
        stack.admin_token,
        "bulk_edit_documents",
        document_ids=documents,
        operation=add,
        dry_run=True,
    )
    unconfirmed = await refused(
        stack, stack.admin_token, "bulk_edit_documents", document_ids=documents, operation=add
    )

    assert (preview["dry_run"], preview["affected_count"], preview["unchanged_count"]) == (
        True,
        2,
        0,
    )
    assert len(cast("list[object]", preview["sample"])) == 2
    assert "Nothing was changed" in unconfirmed
    assert "confirm=true" in unconfirmed
    assert [tags_of(stack, d) for d in documents] == before

    done = await run(
        stack,
        stack.admin_token,
        "bulk_edit_documents",
        document_ids=documents,
        operation=add,
        confirm=True,
    )

    assert (done["applied"], done["affected_count"]) == (True, 2)
    for document, original in zip(documents, before, strict=True):
        assert tags_of(stack, document) == original | {parent, child}

    blocked = await refused(
        stack,
        stack.admin_token,
        "bulk_edit_documents",
        document_ids=documents,
        operation={"op": "remove_tags", "tags": [f"{scratch}parent"]},
        confirm=True,
    )
    assert "also removes its child tags" in blocked
    assert all({parent, child} <= tags_of(stack, d) for d in documents)

    both = {"op": "remove_tags", "tags": [f"{scratch}parent", f"{scratch}child"]}
    _ = await run(
        stack,
        stack.admin_token,
        "bulk_edit_documents",
        document_ids=documents,
        operation=both,
        confirm=True,
    )
    assert all(not ({parent, child} & tags_of(stack, d)) for d in documents)


async def test_references_and_custom_fields_change_without_touching_the_rest(
    stack: Stack, documents: list[int], scratch: str
) -> None:
    correspondent = make(stack, "correspondents", {"name": f"{scratch}corr"})
    text = make(stack, "custom_fields", {"name": f"{scratch}text", "data_type": "string"})
    number = make(stack, "custom_fields", {"name": f"{scratch}number", "data_type": "integer"})
    first, second = documents

    _ = await run(
        stack,
        stack.admin_token,
        "bulk_edit_documents",
        document_ids=documents,
        operation={"op": "set_correspondent", "correspondent": f"{scratch}corr"},
        confirm=True,
    )
    assert [raw(stack, d)["correspondent"] for d in documents] == [correspondent] * 2

    _ = await run(
        stack,
        stack.admin_token,
        "bulk_edit_documents",
        document_ids=documents,
        operation={"op": "modify_custom_fields", "values": {f"{scratch}text": "a"}},
        confirm=True,
    )
    _ = await run(
        stack,
        stack.admin_token,
        "bulk_edit_documents",
        document_ids=[first],
        operation={"op": "modify_custom_fields", "values": {f"{scratch}number": 5}},
        confirm=True,
    )
    assert fields_of(stack, first) == {text: "a", number: 5}
    assert fields_of(stack, second) == {text: "a"}

    removed = await run(
        stack,
        stack.admin_token,
        "bulk_edit_documents",
        document_ids=documents,
        operation={"op": "modify_custom_fields", "remove": [f"{scratch}text"]},
        confirm=True,
    )
    assert removed["affected_count"] == 2
    assert fields_of(stack, first) == {number: 5}

    _ = await run(
        stack,
        stack.admin_token,
        "bulk_edit_documents",
        document_ids=documents,
        operation={"op": "set_correspondent", "correspondent": None},
        confirm=True,
    )
    assert [raw(stack, d)["correspondent"] for d in documents] == [None, None]


async def test_documents_go_to_the_trash_and_reprocessing_is_queued(
    stack: Stack, documents: list[int]
) -> None:
    again = await run(
        stack,
        stack.admin_token,
        "bulk_edit_documents",
        document_ids=documents[:1],
        operation={"op": "reprocess"},
        confirm=True,
    )
    assert again["applied"] is True

    unconfirmed = await refused(
        stack,
        stack.admin_token,
        "bulk_edit_documents",
        document_ids=documents,
        operation={"op": "delete"},
    )
    assert "trash" in unconfirmed
    assert not any(live_in_trash(stack, d) for d in documents)

    done = await run(
        stack,
        stack.admin_token,
        "bulk_edit_documents",
        document_ids=documents,
        operation={"op": "delete"},
        confirm=True,
    )

    assert done["affected_count"] == 2
    assert all(live_in_trash(stack, d) for d in documents)
    assert all(stack.admin.get(f"/api/documents/{d}/").status_code == 404 for d in documents)


async def test_a_missing_document_and_a_restricted_user_change_nothing(
    stack: Stack, documents: list[int], scratch: str
) -> None:
    tag = make(stack, "tags", {"name": f"{scratch}tag"})
    add = {"op": "add_tags", "tags": [tag]}
    public = stack.seeded.public_document
    # The restricted user sees the shared seed taxonomy only.
    inbox = {"op": "remove_tags", "tags": [stack.seeded.inbox_tag]}
    seeded_tags = tags_of(stack, public)

    missing = await refused(
        stack,
        stack.admin_token,
        "bulk_edit_documents",
        document_ids=[*documents, 999_999],
        operation=add,
        confirm=True,
    )
    denied = await refused(
        stack,
        stack.reader_token,
        "bulk_edit_documents",
        document_ids=[public],
        operation=inbox,
        confirm=True,
    )
    preview = await run(
        stack,
        stack.reader_token,
        "bulk_edit_documents",
        document_ids=[public],
        operation=inbox,
        dry_run=True,
    )

    assert "999999" in missing
    assert "nothing was changed" in denied
    assert (preview["dry_run"], preview["affected_count"]) == (True, 1)
    assert all(tag not in tags_of(stack, d) for d in documents)
    assert tags_of(stack, public) == seeded_tags


async def test_taxonomy_permissions_are_set_and_the_objects_deleted_with_confirm(
    stack: Stack, scratch: str
) -> None:
    first = make(stack, "correspondents", {"name": f"{scratch}one"})
    second = make(stack, "correspondents", {"name": f"{scratch}two"})
    reader = num(
        results(stack.admin.get("/api/users/", params={"username__iexact": "e2e-reader"}))[0]["id"]
    )
    group = num(
        results(stack.admin.get("/api/groups/", params={"name__iexact": "e2e-group"}))[0]["id"]
    )
    selection = {"kind": "correspondent", "items": [f"{scratch}one", f"{scratch}two"]}
    grants = {
        "op": "set_permissions",
        "view_users": ["e2e-reader"],
        "view_groups": ["e2e-group"],
    }

    def full(item: int) -> Json:
        found = stack.admin.get(f"/api/correspondents/{item}/", params={"full_perms": "true"})
        return obj(parse(found.raise_for_status()))

    preview = await run(
        stack, stack.admin_token, "bulk_edit_taxonomy", **selection, operation=grants, dry_run=True
    )
    unconfirmed = await refused(
        stack, stack.admin_token, "bulk_edit_taxonomy", **selection, operation=grants
    )
    assert (preview["dry_run"], preview["affected_count"]) == (True, 2)
    assert "Nothing was changed" in unconfirmed
    assert obj(obj(full(first)["permissions"])["view"])["users"] == []

    done = await run(
        stack, stack.admin_token, "bulk_edit_taxonomy", **selection, operation=grants, confirm=True
    )

    assert done["applied"] is True
    for item in (first, second):
        view = obj(obj(full(item)["permissions"])["view"])
        assert (view["users"], view["groups"]) == ([reader], [group])
    visible = {r["id"] for r in results(stack.reader.get("/api/correspondents/"))}
    assert {first, second} <= visible

    delete = {"op": "delete"}
    text = await refused(
        stack, stack.admin_token, "bulk_edit_taxonomy", **selection, operation=delete
    )
    gone = await run(
        stack, stack.admin_token, "bulk_edit_taxonomy", **selection, operation=delete, confirm=True
    )

    assert "delete 2 correspondents" in text
    assert gone["affected_count"] == 2
    assert [stack.admin.get(f"/api/correspondents/{i}/").status_code for i in (first, second)] == [
        404,
        404,
    ]


async def test_a_tag_deletion_names_the_child_tags_that_go_with_it(
    stack: Stack, scratch: str
) -> None:
    parent = make(stack, "tags", {"name": f"{scratch}parent"})
    child = make(stack, "tags", {"name": f"{scratch}child", "parent": parent})

    preview = await run(
        stack,
        stack.admin_token,
        "bulk_edit_taxonomy",
        kind="tag",
        items=[f"{scratch}parent"],
        operation={"op": "delete"},
        dry_run=True,
    )
    _ = await run(
        stack,
        stack.admin_token,
        "bulk_edit_taxonomy",
        kind="tag",
        items=[parent],
        operation={"op": "delete"},
        confirm=True,
    )

    assert preview["also_deleted"] == [f"{scratch}child"]
    assert stack.admin.get(f"/api/tags/{child}/").status_code == 404


async def test_a_read_only_server_offers_neither_bulk_tool(stack: Stack) -> None:
    async with Client(server(stack, stack.admin_token, read_only=True)) as client:
        names = {tool.name for tool in await client.list_tools()}

    assert names.isdisjoint({"bulk_edit_documents", "bulk_edit_taxonomy"})
