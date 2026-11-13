"""upload_document through the in-memory client: one schema per mode, and what each accepts.

Over HTTP (and over stdio without an allowlist) the tool takes base64 content only; over stdio
with PAPERLESS_MCP_UPLOAD_DIRS it takes content or a path as well. The variants are two tools
registered under one name, so the remote one has no path parameter, and no code that reads one.
"""

import base64
from typing import TYPE_CHECKING, cast

import pytest
from e2e.helpers import pdf
from fakes.paperless import FakePaperless, seeded
from fastmcp import Client
from harness import serve

if TYPE_CHECKING:
    from pathlib import Path

    from fastmcp import FastMCP

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-7")]

type Json = dict[str, object]


def writer() -> FakePaperless:
    return FakePaperless(seeded(), user="admin", allow_writes=True)


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def remote(fake: FakePaperless | None = None, **settings: object) -> FastMCP:
    return serve(fake or writer(), **settings)  # pyright: ignore[reportArgumentType] -- settings


def local(directory: Path, fake: FakePaperless | None = None) -> FastMCP:
    return serve(fake or writer(), stdio=True, upload_dirs=[str(directory)])


async def schema(server: FastMCP) -> Json:
    async with Client(server) as client:
        [tool] = [t for t in await client.list_tools() if t.name == "upload_document"]
    return cast("Json", tool.input_schema)


async def call(server: FastMCP, **arguments: object) -> Json:
    async with Client(server) as client:
        result = await client.call_tool("upload_document", arguments)
    assert result.structured_content is not None
    return result.structured_content


async def failure(server: FastMCP, **arguments: object) -> str:
    async with Client(server) as client:
        result = await client.call_tool("upload_document", arguments, raise_on_error=False)
    assert result.is_error
    return str(result.content)


# --- the schema of each mode -------------------------------------------------------------------


async def test_the_remote_tool_takes_content_and_a_name_and_no_path() -> None:
    properties = cast("Json", (await schema(remote()))["properties"])

    assert {"content_base64", "filename"} <= set(properties)
    assert "path" not in properties
    assert (await schema(remote()))["required"] == ["content_base64", "filename"]


async def test_stdio_without_an_allowlist_offers_no_path_either() -> None:
    server = serve(writer(), stdio=True)

    assert "path" not in cast("Json", (await schema(server))["properties"])


async def test_an_allowlist_alone_does_not_offer_a_path_over_http(tmp_path: Path) -> None:
    server = serve(writer(), stdio=False, upload_dirs=[str(tmp_path)])

    assert "path" not in cast("Json", (await schema(server))["properties"])


async def test_stdio_with_an_allowlist_offers_content_or_a_path(tmp_path: Path) -> None:
    description = await schema(local(tmp_path))
    properties = cast("dict[str, Json]", description["properties"])

    assert "path" in properties
    assert "path" in str(properties["path"]["anyOf"])  # pydantic's `format: path`
    assert description.get("required", []) == []
    assert "content_base64" in properties
    assert properties["path"]["description"]


async def test_each_mode_has_exactly_one_upload_tool_with_the_same_annotations(
    tmp_path: Path,
) -> None:
    for server in (remote(), local(tmp_path)):
        async with Client(server) as client:
            tools = [t for t in await client.list_tools() if t.name == "upload_document"]
        [tool] = tools
        assert tool.annotations is not None
        assert (
            tool.annotations.read_only_hint,
            tool.annotations.destructive_hint,
            tool.annotations.idempotent_hint,
            tool.annotations.open_world_hint,
        ) == (False, False, False, False)
        assert tool.annotations.title == "Upload document"


# --- using it ------------------------------------------------------------------------------------


async def test_content_becomes_a_document_and_the_result_names_task_and_document() -> None:
    fake = writer()

    output = await call(
        remote(fake),
        content_base64=b64(pdf("hello")),
        filename="Käse.pdf",
        title="Hello",
        tags=["e2e-invoices"],
        correspondent="e2e-acme",
        custom_fields={"e2e-integer": 3},
    )

    [task] = fake.state.tasks
    assert output["task_id"] == task.task_id
    assert (output["state"], output["wait"], output["filename"]) == (
        "success",
        "not_requested",
        "Käse.pdf",
    )
    assert output["document_id"] == task.document_ids[0]
    assert output["web_url"] == f"http://paperless.test/documents/{task.document_ids[0]}/details"
    created = fake.state.documents[-1]
    assert (created.title, created.file_name, created.correspondent) == ("Hello", "Käse.pdf", 1)


async def test_the_wait_is_passed_on() -> None:
    output = await call(remote(), content_base64=b64(pdf("x")), filename="x.pdf", wait_seconds=30)

    assert output["wait"] == "finished"


async def test_a_wait_below_zero_is_refused_by_the_schema() -> None:
    message = await failure(
        remote(), content_base64=b64(pdf("x")), filename="x.pdf", wait_seconds=-1
    )

    assert "wait_seconds" in message


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"content_base64": "not base64!", "filename": "x.pdf"}, "not valid base64"),
        ({"content_base64": b64(b"\x7fELF" + bytes(64)), "filename": "x.pdf"}, "can consume"),
        ({"content_base64": b64(pdf("x")), "filename": ".."}, "file name"),
        ({"content_base64": b64(pdf("x")), "filename": "x.pdf", "tags": ["no"]}, "No tag is named"),
    ],
)  # fmt: skip
async def test_a_refusal_reaches_the_client_with_its_message_and_hint(
    arguments: Json, message: str
) -> None:
    fake = writer()

    text = await failure(remote(fake), **arguments)

    assert message in text
    assert "Hint:" in text
    assert fake.state.tasks == []


async def test_a_file_over_the_limit_is_refused() -> None:
    server = remote(max_upload_bytes=64)

    text = await failure(server, content_base64=b64(pdf("x") + bytes(64)), filename="x.pdf")

    assert "larger than the limit of 64 bytes" in text


async def test_the_remote_tool_never_reads_a_path_even_when_one_is_sent(tmp_path: Path) -> None:
    secret = tmp_path / "secret.pdf"
    _ = secret.write_bytes(pdf("secret"))
    fake = writer()

    async with Client(serve(fake, upload_dirs=[str(tmp_path)])) as client:
        result = await client.call_tool(
            "upload_document", {"path": str(secret), "filename": "x.pdf"}, raise_on_error=False
        )

    assert result.is_error
    assert fake.state.tasks == []


# --- by path (stdio with an allowlist) -----------------------------------------------------------


async def test_a_path_inside_the_allowlist_is_uploaded(tmp_path: Path) -> None:
    _ = (tmp_path / "scan März.pdf").write_bytes(pdf("from disk"))
    fake = writer()

    output = await call(local(tmp_path, fake), path=str(tmp_path / "scan März.pdf"))

    assert (output["filename"], output["state"]) == ("scan März.pdf", "success")
    assert fake.state.documents[-1].original == pdf("from disk")


async def test_content_still_works_with_an_allowlist(tmp_path: Path) -> None:
    output = await call(local(tmp_path), content_base64=b64(pdf("x")), filename="x.pdf")

    assert output["filename"] == "x.pdf"


@pytest.mark.parametrize(
    "arguments",
    [
        pytest.param({}, id="neither"),
        pytest.param({"content_base64": "QUJD", "path": "/srv/x"}, id="both"),
        pytest.param({"content_base64": "QUJD"}, id="content without a name"),
    ],
)
async def test_exactly_one_source_is_needed_and_content_needs_a_name(
    tmp_path: Path, arguments: Json
) -> None:
    fake = writer()

    text = await failure(local(tmp_path, fake), **arguments)

    assert "Hint:" in text
    assert fake.state.tasks == []


@pytest.mark.parametrize(
    "target", ["/etc/passwd", "../forbidden.pdf", "relative.pdf", "missing.pdf"]
)
async def test_a_path_outside_the_allowlist_or_relative_is_refused(
    tmp_path: Path, target: str
) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    _ = (tmp_path / "forbidden.pdf").write_bytes(pdf("forbidden"))
    fake = writer()
    path = target if target.startswith("/") else f"{allowed}/{target}"
    if target == "relative.pdf":
        path = target

    text = await failure(local(allowed, fake), path=path)

    assert "Hint:" in text
    assert fake.state.tasks == []
    assert "forbidden" not in text


async def test_a_path_sent_with_content_to_the_remote_tool_is_not_read(tmp_path: Path) -> None:
    secret = tmp_path / "secret.pdf"
    _ = secret.write_bytes(pdf("secret"))
    fake = writer()

    async with Client(serve(fake, upload_dirs=[str(tmp_path)])) as client:
        result = await client.call_tool(
            "upload_document",
            {"content_base64": b64(pdf("sent")), "filename": "x.pdf", "path": str(secret)},
            raise_on_error=False,
        )

    uploaded = [d.original for d in fake.state.documents if d.id > 2]
    assert pdf("secret") not in uploaded
    assert result.is_error  # FastMCP refuses an argument the tool does not declare
    assert uploaded == []
