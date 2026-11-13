"""create, update and delete_taxonomy_item through the assembled server on Paperless 3.4.2.

Every object is named `plmcp19-<uuid>...` and deleted again, also when a test fails; documents
are the test's own. Seeded objects are never changed.
"""

import uuid
from typing import TYPE_CHECKING, cast

import pytest
from fastmcp import Client
from harness import paperless_settings, server_settings

from e2e.helpers import consume, obj, parse, pdf, results, token_for
from paperless_mcp.server import create_server

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastmcp import FastMCP
    from fastmcp.client.transports import FastMCPTransport

    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio, pytest.mark.usefixtures("scratch")]

type Json = dict[str, object]

PREFIX = "plmcp19-"
PATHS = (
    "/api/tags/",
    "/api/correspondents/",
    "/api/document_types/",
    "/api/storage_paths/",
    "/api/custom_fields/",
)
KIND_ARGUMENTS: dict[str, Json] = {
    "tag": {"colour": "#123456"},
    "correspondent": {"matching_algorithm": "any", "match": "acme"},
    "document_type": {"matching_algorithm": "literal", "match": "invoice"},
    "storage_path": {"path": "plmcp19/{{ created_year }}/{{ title }}"},
    "custom_field": {"data_type": "select", "select_options": ["one", "two"]},
}


def new_name(suffix: str = "") -> str:
    return f"{PREFIX}{uuid.uuid4().hex}{suffix}"


@pytest.fixture
def scratch(stack: Stack) -> Iterator[None]:
    """Delete every object the test created (their names start with PREFIX)."""
    yield
    for path in PATHS:
        for row in results(stack.admin.get(path, params={"name__istartswith": PREFIX})):
            _ = stack.admin.delete(f"{path}{row['id']}/")


@pytest.fixture
def document(stack: Stack) -> Iterator[int]:
    title = f"plmcp19-doc-{uuid.uuid4().hex}"
    _, created = consume(stack.admin, title, pdf(title))
    assert created is not None
    yield created
    _ = stack.admin.delete(f"/api/documents/{created}/")


def server(stack: Stack, token: str, *, read_only: bool = False) -> FastMCP:
    return create_server(
        paperless_settings(stack.base_url, token), server_settings(read_only=read_only)
    )


async def call(client: Client[FastMCPTransport], tool: str, **arguments: object) -> Json:
    result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    return result.structured_content


async def error(client: Client[FastMCPTransport], tool: str, **arguments: object) -> str:
    result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error
    return str(result.content)


def rows(listing: Json) -> list[Json]:
    return cast("list[Json]", listing["items"])


def options(detail: Json) -> list[tuple[str, str]]:
    return [(str(o["id"]), str(o["label"])) for o in cast("list[Json]", detail["select_options"])]


@pytest.mark.parametrize("kind", list(KIND_ARGUMENTS))
async def test_an_object_of_every_kind_is_created_renamed_and_deleted_with_confirm(
    stack: Stack, kind: str
) -> None:
    name, renamed = new_name(), new_name("-renamed")
    async with Client(server(stack, stack.admin_token)) as client:
        before = await call(client, "list_taxonomy", kind=kind, name_contains=PREFIX)
        created = await call(
            client, "create_taxonomy_item", kind=kind, name=name, **KIND_ARGUMENTS[kind]
        )
        listed = await call(client, "list_taxonomy", kind=kind, name_contains=name)
        changed = await call(client, "update_taxonomy_item", kind=kind, item=name, name=renamed)
        found = await call(client, "list_taxonomy", kind=kind, name_contains=renamed)
        preview = await error(client, "delete_taxonomy_item", kind=kind, item=renamed)
        still = await call(client, "list_taxonomy", kind=kind, name_contains=renamed)
        deleted = await call(
            client, "delete_taxonomy_item", kind=kind, item=changed["id"], confirm=True
        )
        gone = await call(client, "list_taxonomy", kind=kind, name_contains=renamed)

    assert before["count"] == 0
    assert created["name"] == name
    assert [r["id"] for r in rows(listed)] == [created["id"]]
    assert (changed["id"], changed["name"]) == (created["id"], renamed)
    assert [r["id"] for r in rows(found)] == [created["id"]]
    assert "Nothing was changed. Confirming would delete" in preview
    assert still["count"] == 1
    assert "was deleted" in str(deleted["message"])
    assert gone["count"] == 0


async def test_the_tag_hierarchy_is_kept_sane_and_a_delete_takes_the_children(
    stack: Stack, document: int
) -> None:
    top, middle = new_name("-top"), new_name("-middle")
    async with Client(server(stack, stack.admin_token)) as client:
        parent = await call(client, "create_taxonomy_item", kind="tag", name=top)
        child = await call(
            client, "create_taxonomy_item", kind="tag", name=middle, parent=top.upper()
        )
        cycle = await error(client, "update_taxonomy_item", kind="tag", item=top, parent=middle)
        itself = await error(client, "update_taxonomy_item", kind="tag", item=top, parent=top)
        _ = await call(client, "update_document", document_id=document, tags_set=[middle])
        preview = await error(client, "delete_taxonomy_item", kind="tag", item=top)
        freed = await call(
            client, "update_taxonomy_item", kind="tag", item=middle, clear_parent=True
        )
        moved = await call(
            client, "update_taxonomy_item", kind="tag", item=middle, parent=parent["id"]
        )
        deleted = await call(client, "delete_taxonomy_item", kind="tag", item=top, confirm=True)

    assert child["parent"] == parent["id"]
    assert "descendant" in cycle
    assert "itself" in itself
    assert f"its child tags '{middle} (1 document)' are deleted with it" in preview
    assert "it is removed from its documents (1 document)" in preview
    assert (freed["parent"], moved["parent"]) == (None, parent["id"])
    assert deleted["also_deleted"] == [{"name": middle, "document_count": 1}]
    raw = obj(parse(stack.admin.get(f"/api/documents/{document}/", params={"fields": "tags"})))
    assert raw["tags"] == []
    assert results(stack.admin.get("/api/tags/", params={"name__istartswith": PREFIX})) == []


async def test_select_options_keep_their_ids_and_the_documents_that_hold_them(
    stack: Stack, document: int
) -> None:
    name = new_name()
    async with Client(server(stack, stack.admin_token)) as client:
        created = await call(
            client,
            "create_taxonomy_item",
            kind="custom_field",
            name=name,
            data_type="select",
            select_options=["one", "two"],
        )
        [(one, _), (two, _)] = options(created)
        _ = await call(client, "update_document", document_id=document, custom_fields={name: "two"})
        renamed = await call(
            client,
            "update_taxonomy_item",
            kind="custom_field",
            item=name,
            select_options_rename={"two": "TWO"},
            select_options_add=["three"],
        )
        detail = await call(client, "get_document", document_id=document)
        field_renamed = await call(
            client, "update_taxonomy_item", kind="custom_field", item=name, name=f"{name}-b"
        )
        refused = await error(
            client,
            "update_taxonomy_item",
            kind="custom_field",
            item=name + "-b",
            select_options_remove=["one"],
        )
        holding = await error(
            client,
            "update_taxonomy_item",
            kind="custom_field",
            item=f"{name}-b",
            select_options_remove=["TWO"],
        )
        removed = await call(
            client,
            "update_taxonomy_item",
            kind="custom_field",
            item=f"{name}-b",
            select_options_remove=["one"],
            confirm=True,
        )

    assert [o[0] for o in options(renamed)[:2]] == [one, two]
    assert [o[1] for o in options(renamed)] == ["one", "TWO", "three"]
    [value] = [f for f in cast("list[Json]", detail["custom_fields"]) if f["name"] == name]
    assert value["value"] == "TWO"
    assert options(field_renamed) == options(renamed)
    assert "confirm=true" in refused
    assert "remove the option(s) 'one' of" in refused
    assert ": 0 documents you can see hold a removed option" in refused
    assert ": 1 document you can see hold a removed option" in holding
    assert [o[1] for o in options(removed)] == ["TWO", "three"]
    stored = obj(parse(stack.admin.get(f"/api/custom_fields/{created['id']}/")))
    assert [o["id"] for o in cast("list[Json]", obj(stored["extra_data"])["select_options"])] == [
        two,
        options(renamed)[2][0],
    ]


async def test_what_paperless_refuses_comes_back_with_a_hint_where_we_can_help(
    stack: Stack,
) -> None:
    async with Client(server(stack, stack.admin_token)) as client:
        template = await error(
            client,
            "create_taxonomy_item",
            kind="storage_path",
            name=new_name(),
            path="{{ no_such_variable }}",
        )
        pattern = await error(
            client,
            "create_taxonomy_item",
            kind="correspondent",
            name=new_name(),
            matching_algorithm="regex",
            match="(",
        )
        name = new_name()
        _ = await call(client, "create_taxonomy_item", kind="correspondent", name=name)
        twice = await error(client, "create_taxonomy_item", kind="correspondent", name=name)

    assert "path" in template
    assert "created_year" in template
    assert "regular expression" in pattern
    assert "unique" in twice


async def test_a_user_without_the_permissions_cannot_write(stack: Stack) -> None:
    name = new_name()
    _ = stack.admin.post("/api/tags/", json={"name": name, "owner": None}).raise_for_status()
    async with Client(server(stack, stack.reader_token)) as client:
        created = await error(client, "create_taxonomy_item", kind="tag", name=new_name())
        updated = await error(client, "update_taxonomy_item", kind="tag", item=name, name="x")
        preview = await error(client, "delete_taxonomy_item", kind="tag", item=name)
        deleted = await error(client, "delete_taxonomy_item", kind="tag", item=name, confirm=True)

    for text in (created, updated, deleted):
        assert "may not perform" in text
    assert "Nothing was changed. Confirming would delete tag" in preview


async def test_read_only_mode_hides_the_write_tools(stack: Stack) -> None:
    async with Client(server(stack, stack.admin_token, read_only=True)) as client:
        names = {tool.name for tool in await client.list_tools()}

    assert names.isdisjoint(
        {"create_taxonomy_item", "update_taxonomy_item", "delete_taxonomy_item"}
    )


@pytest.fixture
def limited_user(stack: Stack) -> Iterator[tuple[str, int]]:
    """A user who may view, add, change and delete tags; deleted afterwards."""
    username, password = f"plmcp19-{uuid.uuid4().hex[:8]}", f"Pw-{uuid.uuid4().hex}"
    permissions = [f"{action}_tag" for action in ("view", "add", "change", "delete")]
    created = stack.admin.post(
        "/api/users/",
        json={"username": username, "password": password, "user_permissions": permissions},
    ).raise_for_status()
    user_id = int(str(obj(parse(created))["id"]))
    yield token_for(username, password), user_id
    _ = stack.admin.delete(f"/api/users/{user_id}/")


async def test_deleting_a_tag_also_deletes_child_tags_the_caller_cannot_see(
    stack: Stack, limited_user: tuple[str, int]
) -> None:
    token, _ = limited_user
    mine, theirs = new_name("-mine"), new_name("-theirs")
    async with Client(server(stack, token)) as client_:
        parent = await call(client_, "create_taxonomy_item", kind="tag", name=mine)
        child = stack.admin.post(
            "/api/tags/", json={"name": theirs, "parent": parent["id"]}
        ).raise_for_status()
        preview = await error(client_, "delete_taxonomy_item", kind="tag", item=mine)
        deleted = await call(client_, "delete_taxonomy_item", kind="tag", item=mine, confirm=True)

    assert "child tags you cannot see (other users') are deleted with it as well" in preview
    assert "was deleted" in str(deleted["message"])
    assert stack.admin.get(f"/api/tags/{obj(parse(child))['id']}/").status_code == 404
