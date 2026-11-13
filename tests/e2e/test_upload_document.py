"""upload_document on Paperless 3.4.2: a PDF goes in through the tool and is found afterwards.

Each test uploads documents of its own (unique title and content). The fixture waits for every
task it was told about to end, then deletes the documents and empties their trash entries, so
nothing stays on the shared stack, also when a test fails.
"""

import base64
import shutil
import time
import uuid
from typing import TYPE_CHECKING, cast

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from harness import paperless_settings, server_settings

from e2e.helpers import CONSUME_TIMEOUT, num, pdf, results, seq
from paperless_mcp.server import create_server

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from fastmcp import FastMCP

    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]

type Json = dict[str, object]


class Uploads:
    """The tasks of a test's uploads, cleaned up at its end."""

    def __init__(self, stack: Stack) -> None:
        self.stack: Stack = stack
        self.tasks: list[str] = []

    def title(self) -> str:
        return f"plmcp18-{uuid.uuid4().hex}"

    def clean_up(self) -> None:
        documents: list[int] = []
        for task_id in self.tasks:
            deadline = time.monotonic() + CONSUME_TIMEOUT
            while time.monotonic() < deadline:
                found = results(self.stack.admin.get("/api/tasks/", params={"task_id": task_id}))
                if found and found[0]["status"] in {"success", "failure", "revoked"}:
                    documents += [num(d) for d in seq(found[0]["related_document_ids"])]
                    break
                time.sleep(1)
        for document in documents:
            _ = self.stack.admin.delete(f"/api/documents/{document}/")
        if documents:
            _ = self.stack.admin.post(
                "/api/trash/", json={"action": "empty", "documents": documents}
            )


@pytest.fixture
def uploads(stack: Stack) -> Iterator[Uploads]:
    scope = Uploads(stack)
    yield scope
    scope.clean_up()


def remote(stack: Stack, token: str) -> FastMCP:
    """The server as HTTP clients get it: content only."""
    return create_server(paperless_settings(stack.base_url, token), server_settings())


async def upload(scope: Uploads, server: FastMCP, **arguments: object) -> Json:
    async with Client(server) as client:
        result = await client.call_tool("upload_document", arguments)
    assert result.structured_content is not None
    output: Json = result.structured_content
    scope.tasks.append(str(output["task_id"]))
    return output


async def search(server: FastMCP, title: str) -> list[Json]:
    async with Client(server) as client:
        result = await client.call_tool("search_documents", {"title_contains": title})
    assert result.structured_content is not None
    return cast("list[Json]", result.structured_content["documents"])


async def test_a_pdf_uploaded_as_content_is_consumed_and_found_afterwards(
    stack: Stack, uploads: Uploads
) -> None:
    server = remote(stack, stack.admin_token)
    title = uploads.title()

    output = await upload(
        uploads,
        server,
        content_base64=base64.b64encode(pdf(title)).decode(),
        filename=f"{title} Käse.pdf",
        title=title,
        correspondent="e2e-acme",
        document_type="e2e-invoice",
        tags=["e2e-invoices"],
        custom_fields={"e2e-integer": 7},
        wait_seconds=30,
    )
    found = await search(server, title)

    assert (output["state"], output["wait"], output["message"]) == ("success", "finished", None)
    assert output["filename"] == f"{title} Käse.pdf"
    assert output["mime_type"] == "application/pdf"
    document = num(output["document_id"])
    assert [row["id"] for row in found] == [document]
    assert str(output["web_url"]).endswith(f"/documents/{document}/details")
    row = found[0]
    assert cast("Json", row["correspondent"])["name"] == "e2e-acme"
    assert {t["name"] for t in cast("list[Json]", row["tags"])} >= {"e2e-invoices", "e2e-finance"}
    async with Client(server) as client:
        detail = await client.call_tool("get_document", {"document_id": document})
    assert detail.structured_content is not None
    fields = {
        f["name"]: f["value"]
        for f in cast("list[Json]", detail.structured_content["custom_fields"])
    }
    assert fields == {"e2e-integer": 7}


async def test_without_a_wait_the_task_id_comes_back_at_once_and_get_task_follows_it(
    stack: Stack, uploads: Uploads
) -> None:
    server = remote(stack, stack.admin_token)
    title = uploads.title()

    output = await upload(
        uploads, server, content_base64=base64.b64encode(pdf(title)).decode(), filename="a.pdf"
    )
    async with Client(server) as client:
        followed = await client.call_tool(
            "get_task", {"task_id": output["task_id"], "wait_seconds": 30}
        )

    assert output["wait"] == "not_requested"
    assert output["state"] in {"pending", "started", "success"}
    assert followed.structured_content is not None
    assert followed.structured_content["state"] == "success"


async def test_a_taken_archive_serial_number_shows_up_as_a_failed_task(
    stack: Stack, uploads: Uploads
) -> None:
    server = remote(stack, stack.admin_token)
    serial = 3_000_000_000 + uuid.uuid4().int % 1_000_000_000
    first, second = uploads.title(), uploads.title()

    one = await upload(
        uploads, server, content_base64=base64.b64encode(pdf(first)).decode(),
        filename="a.pdf", archive_serial_number=serial, wait_seconds=30,
    )  # fmt: skip
    two = await upload(
        uploads, server, content_base64=base64.b64encode(pdf(second)).decode(),
        filename="b.pdf", archive_serial_number=serial, wait_seconds=30,
    )  # fmt: skip

    assert one["state"] == "success"
    assert (two["state"], two["document_id"]) == ("failure", None)
    assert "ASN" in str(two["message"])


async def test_the_http_server_refuses_what_paperless_cannot_consume(stack: Stack) -> None:
    server = remote(stack, stack.admin_token)

    async with Client(server) as client:
        result = await client.call_tool(
            "upload_document",
            {
                "content_base64": base64.b64encode(b"\x7fELF" + bytes(64)).decode(),
                "filename": "x.pdf",
            },
            raise_on_error=False,
        )

    assert result.is_error
    assert "not a type Paperless can consume" in str(result.content)


async def test_a_user_without_the_permission_to_add_documents_is_told_so(stack: Stack) -> None:
    server = remote(stack, stack.reader_token)

    async with Client(server) as client:
        result = await client.call_tool(
            "upload_document",
            {"content_base64": base64.b64encode(pdf("no")).decode(), "filename": "x.pdf"},
            raise_on_error=False,
        )

    assert result.is_error
    assert "may not perform" in str(result.content)


async def test_the_console_script_uploads_a_file_by_path_over_stdio(
    stack: Stack, uploads: Uploads, tmp_path: Path
) -> None:
    title = uploads.title()
    folder = tmp_path / "inbox"
    folder.mkdir()
    _ = (folder / f"{title} März.pdf").write_bytes(pdf(title))
    _ = (tmp_path / "outside.pdf").write_bytes(pdf("outside"))
    script = shutil.which("paperless-mcp")
    assert script, "the paperless-mcp console script is not installed (uv sync)"
    transport = StdioTransport(
        script,
        [],
        env={
            "PAPERLESS_URL": stack.base_url,
            "PAPERLESS_TOKEN": stack.admin_token,
            "PAPERLESS_MCP_UPLOAD_DIRS": str(folder),
        },
        keep_alive=False,
    )

    async with Client(transport) as client:
        [tool] = [t for t in await client.list_tools() if t.name == "upload_document"]
        done = await client.call_tool(
            "upload_document",
            {"path": str(folder / f"{title} März.pdf"), "title": title, "wait_seconds": 30},
        )
        refused = await client.call_tool(
            "upload_document", {"path": str(tmp_path / "outside.pdf")}, raise_on_error=False
        )

    assert "path" in cast("Json", tool.input_schema["properties"])
    assert done.structured_content is not None
    output: Json = done.structured_content
    uploads.tasks.append(str(output["task_id"]))
    assert (output["state"], output["filename"]) == ("success", f"{title} März.pdf")
    found = results(stack.admin.get("/api/documents/", params={"title__iexact": title}))
    assert [num(d["id"]) for d in found] == [output["document_id"]]
    assert refused.is_error
    assert "No file was found at that path" in str(refused.content)
