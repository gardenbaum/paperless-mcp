"""The versions toolset through the assembled server on Paperless 3.4.2, on documents of its own.

Titles start with `plmcp24-`. Every document and version a test made is deleted again at its end
and its trash entry emptied, so nothing is left on the shared stack, also when a test fails.
"""

import base64
import shutil
import uuid
from typing import TYPE_CHECKING, cast

import pytest
from contract.conftest import delete_and_empty_trash, live_in_trash
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from harness import paperless_settings, server_settings

from e2e.helpers import consume, num, pdf
from paperless_mcp.server import create_server

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from fastmcp import FastMCP

    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]

type Json = dict[str, object]


class Scope:
    """The documents of one test: every id it made, deleted again at its end."""

    def __init__(self, stack: Stack) -> None:
        self.stack: Stack = stack
        self.ids: list[int] = []

    def document(self) -> int:
        title = f"plmcp24-{uuid.uuid4().hex}"
        _, created = consume(self.stack.admin, title, pdf(title))
        assert created is not None
        self.ids.append(created)
        return created


@pytest.fixture
def scope(stack: Stack) -> Iterator[Scope]:
    made = Scope(stack)
    yield made
    delete_and_empty_trash(stack, made.ids)


def server(stack: Stack, token: str, *, read_only: bool = False) -> FastMCP:
    return create_server(
        paperless_settings(stack.base_url, token),
        server_settings(toolsets=["versions"], read_only=read_only),
    )


async def run(stack: Stack, tool: str, **arguments: object) -> Json:
    async with Client(server(stack, stack.admin_token)) as client:
        result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    return result.structured_content


async def refused(stack: Stack, tool: str, **arguments: object) -> str:
    async with Client(server(stack, stack.admin_token)) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error
    return str(result.content)


def text(content: str) -> str:
    return base64.b64encode(content.encode()).decode()


async def versions(stack: Stack, document: int) -> list[Json]:
    output = await run(stack, "list_document_versions", document_id=document)
    return cast("list[Json]", output["versions"])


async def test_a_version_is_uploaded_listed_labelled_and_deleted_only_with_confirm(
    stack: Stack, scope: Scope
) -> None:
    document = scope.document()

    uploaded = await run(
        stack,
        "upload_document_version",
        document_id=document,
        content_base64=text("plmcp24 second version"),
        filename="second.txt",
        label="second",
        wait_seconds=30,
    )
    version = num(uploaded["version_id"])
    scope.ids.append(version)
    assert (uploaded["state"], uploaded["wait"], uploaded["document_id"]) == (
        "success",
        "finished",
        document,
    )
    listing = await run(stack, "list_document_versions", document_id=document)
    assert listing["current_version_id"] == version
    found = cast("list[Json]", listing["versions"])
    assert [(v["id"], v["version_label"], v["is_root"]) for v in found] == [
        (version, "second", False),
        (document, None, True),
    ]
    assert found[0]["checksum"] != found[1]["checksum"]

    labelled = await run(
        stack,
        "set_document_version_label",
        document_id=document,
        version_id=version,
        label="renamed",
    )
    assert cast("Json", labelled["version"])["version_label"] == "renamed"

    unconfirmed = await refused(
        stack, "delete_document_version", document_id=document, version_id=version
    )
    assert "Nothing was changed" in unconfirmed
    assert "confirm=true" in unconfirmed
    assert len(await versions(stack, document)) == 2
    root = await refused(
        stack, "delete_document_version", document_id=document, version_id=document, confirm=True
    )
    assert "root version" in root

    deleted = await run(
        stack, "delete_document_version", document_id=document, version_id=version, confirm=True
    )
    assert (deleted["version_id"], deleted["current_version_id"]) == (version, document)
    assert [v["id"] for v in await versions(stack, document)] == [document]
    assert live_in_trash(stack, version)  # not gone for good


async def test_documents_are_merged_as_versions_only_with_confirm(
    stack: Stack, scope: Scope
) -> None:
    root, first, second = scope.document(), scope.document(), scope.document()

    unconfirmed = await refused(
        stack,
        "merge_documents_as_versions",
        root_document_id=root,
        source_document_ids=[first, second],
    )
    assert "Nothing was changed" in unconfirmed
    assert f"merge document {first}" in unconfirmed
    assert "vanish" in unconfirmed
    assert len(await versions(stack, root)) == 1
    assert len(await versions(stack, first)) == 1  # not merged yet: still a document of its own

    merged = await run(
        stack,
        "merge_documents_as_versions",
        root_document_id=root,
        source_document_ids=[first, second],
        confirm=True,
    )

    assert merged["merged_document_ids"] == [first, second]
    found = cast("list[Json]", merged["versions"])
    assert [v["id"] for v in found] == [second, first, root]
    gone = await refused(stack, "list_document_versions", document_id=first)
    assert f"No document {first}" in gone


async def test_a_read_only_user_may_list_but_not_change_versions(
    stack: Stack, scope: Scope
) -> None:
    document = scope.document()
    version = num(
        (
            await run(
                stack,
                "upload_document_version",
                document_id=document,
                content_base64=text("plmcp24 shared version"),
                filename="shared.txt",
                wait_seconds=30,
            )
        )["version_id"]
    )
    scope.ids.append(version)
    reader_id = num(
        cast(
            "dict[str, Json]",
            stack.reader.get("/api/ui_settings/").json(),
        )["user"]["id"]
    )
    admin_id = num(
        cast(
            "dict[str, Json]",
            stack.admin.get("/api/ui_settings/").json(),
        )["user"]["id"]
    )
    _ = stack.admin.patch(
        f"/api/documents/{document}/",
        json={
            "owner": admin_id,
            "set_permissions": {
                "view": {"users": [reader_id], "groups": []},
                "change": {"users": [], "groups": []},
            },
        },
    ).raise_for_status()

    async with Client(server(stack, stack.reader_token)) as client:
        listed = await client.call_tool("list_document_versions", {"document_id": document})
        denied = await client.call_tool(
            "set_document_version_label",
            {"document_id": document, "version_id": version, "label": "no"},
            raise_on_error=False,
        )

    assert listed.structured_content is not None
    assert len(cast("list[Json]", listed.structured_content["versions"])) == 2
    assert denied.is_error
    assert "may not perform" in str(denied.content)
    assert (await versions(stack, document))[0]["version_label"] is None


async def test_the_console_script_uploads_a_version_by_path_over_stdio(
    stack: Stack, scope: Scope, tmp_path: Path
) -> None:
    document = scope.document()
    folder = tmp_path / "inbox"
    folder.mkdir()
    _ = (folder / "plmcp24 März.txt").write_bytes(b"plmcp24 version from disk")
    _ = (tmp_path / "outside.txt").write_bytes(b"outside")
    script = shutil.which("paperless-mcp")
    assert script, "the paperless-mcp console script is not installed (uv sync)"
    transport = StdioTransport(
        script,
        [],
        env={
            "PAPERLESS_URL": stack.base_url,
            "PAPERLESS_TOKEN": stack.admin_token,
            "PAPERLESS_MCP_TOOLSETS": "versions",
            "PAPERLESS_MCP_UPLOAD_DIRS": str(folder),
        },
        keep_alive=False,
    )

    async with Client(transport) as client:
        [tool] = [t for t in await client.list_tools() if t.name == "upload_document_version"]
        done = await client.call_tool(
            "upload_document_version",
            {
                "document_id": document,
                "path": str(folder / "plmcp24 März.txt"),
                "label": "disk",
                "wait_seconds": 30,
            },
        )
        denied = await client.call_tool(
            "upload_document_version",
            {"document_id": document, "path": str(tmp_path / "outside.txt")},
            raise_on_error=False,
        )

    assert "path" in cast("Json", tool.input_schema["properties"])
    assert done.structured_content is not None
    output: Json = done.structured_content
    scope.ids.append(num(output["version_id"]))
    assert (output["state"], output["filename"]) == ("success", "plmcp24 März.txt")
    assert [v["version_label"] for v in await versions(stack, document)] == ["disk", None]
    assert denied.is_error
    assert "No file was found at that path" in str(denied.content)
