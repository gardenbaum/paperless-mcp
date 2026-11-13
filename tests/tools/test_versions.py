"""The versions toolset through the in-memory client on the seeded fake (PLMCP-24).

The toolset is off by default; upload_document_version has one schema per mode like
upload_document (base64 content everywhere, a path only over stdio with an allowlist).
"""

import base64
from typing import TYPE_CHECKING, cast

import pytest
from fakes.paperless import FakeDocument, FakePaperless, FakeState, seeded
from fastmcp import Client
from harness import serve

from paperless_mcp.domain.versions import VersionUpload

if TYPE_CHECKING:
    from pathlib import Path

    from fastmcp import FastMCP

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-6")]

type Json = dict[str, object]

TOOLS = frozenset(
    {
        "list_document_versions",
        "upload_document_version",
        "set_document_version_label",
        "delete_document_version",
        "merge_documents_as_versions",
    }
)
CONFIRM_DESCRIPTION = (
    "Must be true to go ahead. Without it nothing changes and the error says what would "
    "happen. Describe that to the user and pass true only once they agree, or if they "
    "already asked for exactly this."
)
TEXT = b"plain text version"


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def fake(state: FakeState | None = None, *, writes: bool = True) -> FakePaperless:
    return FakePaperless(state or seeded(), user="admin", allow_writes=writes)


def server(source: FakePaperless, **settings: object) -> FastMCP:
    return serve(source, toolsets=["versions"], **settings)  # pyright: ignore[reportArgumentType] -- settings


async def add_version(source: FakePaperless, document: int = 1, label: str | None = None) -> int:
    upload = VersionUpload(filename="v.txt", mime_type="text/plain", content=TEXT, label=label)
    task = await source.upload_document_version(document, upload)
    [version] = (await source.get_task(task)).document_ids
    return version


async def call(source: FakePaperless, tool: str, **arguments: object) -> Json:
    async with Client(server(source)) as client:
        result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    return result.structured_content


async def refusal(source: FakePaperless, tool: str, **arguments: object) -> str:
    async with Client(server(source)) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error
    return str(result.content)


# --- the toolset -------------------------------------------------------------------------------


async def test_the_versions_toolset_is_off_by_default_and_has_five_tools_when_selected() -> None:
    async with Client(serve(fake())) as client:
        default = {t.name for t in await client.list_tools()}
    async with Client(server(fake())) as client:
        selected = {t.name for t in await client.list_tools()}

    assert not TOOLS & default
    assert selected == TOOLS


async def test_read_only_mode_leaves_only_the_list() -> None:
    async with Client(serve(fake(), toolsets=["versions"], read_only=True)) as client:
        offered = {t.name for t in await client.list_tools()}

    assert offered == {"list_document_versions"}


async def test_the_annotations_tell_which_tool_destroys_and_which_can_repeat() -> None:
    async with Client(server(fake())) as client:
        tools = {t.name: t.annotations for t in await client.list_tools()}

    hints = {
        name: (a.read_only_hint, a.destructive_hint, a.idempotent_hint, a.open_world_hint)
        for name, a in tools.items()
        if a
    }
    assert hints == {
        "list_document_versions": (True, None, True, False),
        "upload_document_version": (False, False, False, False),
        "set_document_version_label": (False, False, True, False),
        "delete_document_version": (False, True, True, False),
        "merge_documents_as_versions": (False, True, False, False),
    }


async def test_the_destructive_tools_take_a_described_confirm_that_defaults_to_false() -> None:
    async with Client(server(fake())) as client:
        tools = {t.name: t for t in await client.list_tools()}

    for name in ("delete_document_version", "merge_documents_as_versions"):
        properties = cast("dict[str, Json]", tools[name].input_schema["properties"])
        assert properties["confirm"]["default"] is False
        assert properties["confirm"]["description"] == CONFIRM_DESCRIPTION
        assert "confirm" not in tools[name].input_schema.get("required", [])
        assert "confirm" in (tools[name].description or "")


async def test_the_descriptions_say_what_a_version_is_and_where_things_go() -> None:
    async with Client(server(fake())) as client:
        listed = {t.name: t for t in await client.list_tools()}
    tools = {name: tool.description or "" for name, tool in listed.items()}

    assert "newest" in tools["list_document_versions"]
    assert "trash" in tools["delete_document_version"]
    assert "root" in tools["delete_document_version"]
    assert "for good" in tools["merge_documents_as_versions"]
    assert "metadata" in tools["merge_documents_as_versions"]
    assert "wait_seconds" in tools["upload_document_version"]
    label = cast("dict[str, Json]", listed["set_document_version_label"].input_schema["properties"])
    assert "64 characters" in str(label["label"]["description"])


# --- reading and labelling ---------------------------------------------------------------------


async def test_the_versions_are_listed_newest_first() -> None:
    source = fake()
    version = await add_version(source, label="draft")

    output = await call(source, "list_document_versions", document_id=1)

    assert (output["document_id"], output["title"], output["current_version_id"]) == (
        1,
        "e2e-public-invoice",
        version,
    )
    versions = cast("list[Json]", output["versions"])
    assert [(v["id"], v["version_label"], v["is_root"]) for v in versions] == [
        (version, "draft", False),
        (1, None, True),
    ]
    assert output["web_url"] == "http://paperless.test/documents/1/details"


async def test_listing_the_versions_of_a_missing_document_is_a_tool_error() -> None:
    text = await refusal(fake(), "list_document_versions", document_id=999)

    assert "No document 999" in text


async def test_a_label_is_set_and_cleared() -> None:
    source = fake()
    version = await add_version(source)

    set_ = await call(
        source, "set_document_version_label", document_id=1, version_id=version, label="final"
    )
    cleared = await call(
        source, "set_document_version_label", document_id=1, version_id=version, label=""
    )

    assert cast("Json", set_["version"])["version_label"] == "final"
    assert cast("Json", cleared["version"])["version_label"] is None
    assert set_["document_id"] == 1


async def test_a_label_that_is_too_long_is_a_tool_error_that_says_how_long() -> None:
    source = fake()
    version = await add_version(source)

    text = await refusal(
        source, "set_document_version_label", document_id=1, version_id=version, label="x" * 65
    )

    assert "at most 64 characters" in text
    assert "Hint:" in text


# --- deleting a version -----------------------------------------------------------------------


async def test_deleting_a_version_without_confirm_changes_nothing_and_says_how_to_confirm() -> None:
    state = seeded()
    version = await add_version(fake(state))

    text = await refusal(
        fake(state, writes=False), "delete_document_version", document_id=1, version_id=version
    )

    assert "Nothing was changed" in text
    assert f"version {version}" in text
    assert "trash" in text
    assert "confirm=true" in text
    assert [v.id for v in state.documents[0].versions] == [version, 1]


async def test_deleting_a_version_with_confirm_removes_it_and_names_the_current_one() -> None:
    state = seeded()
    version = await add_version(fake(state))

    output = await call(
        fake(state), "delete_document_version", document_id=1, version_id=version, confirm=True
    )

    assert (output["version_id"], output["current_version_id"]) == (version, 1)
    assert "trash" in str(output["message"])
    assert [v.id for v in state.documents[0].versions] == [1]


async def test_the_root_version_cannot_be_deleted() -> None:
    text = await refusal(
        fake(), "delete_document_version", document_id=1, version_id=1, confirm=True
    )

    assert "root version" in text
    assert "delete_document" in text


# --- merging -----------------------------------------------------------------------------------


def with_documents(state: FakeState, *numbers: int) -> None:
    state.documents.extend(FakeDocument(id=n, title=f"merge me {n}", owner=None) for n in numbers)


async def test_merging_without_confirm_changes_nothing_and_says_how_to_confirm() -> None:
    state = seeded()
    with_documents(state, 10)

    text = await refusal(
        fake(state, writes=False),
        "merge_documents_as_versions",
        root_document_id=1,
        source_document_ids=[10],
    )

    assert "Nothing was changed" in text
    assert "merge document 10 'merge me 10' into document 1" in text
    assert "vanish" in text
    assert "confirm=true" in text
    assert 10 in {d.id for d in state.documents}


async def test_merging_with_confirm_makes_versions_and_removes_the_sources() -> None:
    state = seeded()
    with_documents(state, 10, 11)

    output = await call(
        fake(state),
        "merge_documents_as_versions",
        root_document_id=1,
        source_document_ids=[10, 11],
        confirm=True,
    )

    assert (output["document_id"], output["merged_document_ids"]) == (1, [10, 11])
    versions = cast("list[Json]", output["versions"])
    assert [v["id"] for v in versions] == [11, 10, 1]
    assert {d.id for d in state.documents}.isdisjoint({10, 11})


async def test_a_merge_label_is_passed_on_for_one_document() -> None:
    state = seeded()
    with_documents(state, 10)

    output = await call(
        fake(state),
        "merge_documents_as_versions",
        root_document_id=1,
        source_document_ids=[10],
        label="scan",
        confirm=True,
    )

    assert [v["version_label"] for v in cast("list[Json]", output["versions"])] == ["scan", None]


async def test_an_invalid_merge_is_a_tool_error_even_with_confirm() -> None:
    text = await refusal(
        fake(),
        "merge_documents_as_versions",
        root_document_id=1,
        source_document_ids=[1],
        confirm=True,
    )

    assert "cannot be merged into itself" in text


# --- uploading a version -----------------------------------------------------------------------


async def schema(source: FastMCP) -> Json:
    async with Client(source) as client:
        [tool] = [t for t in await client.list_tools() if t.name == "upload_document_version"]
    return cast("Json", tool.input_schema)


def local(directory: Path, source: FakePaperless | None = None) -> FastMCP:
    return serve(source or fake(), toolsets=["versions"], stdio=True, upload_dirs=[str(directory)])


async def test_the_remote_tool_takes_content_and_a_name_and_no_path() -> None:
    description = await schema(server(fake()))

    assert "path" not in cast("Json", description["properties"])
    assert description["required"] == ["document_id", "content_base64", "filename"]


async def test_stdio_without_an_allowlist_and_http_with_one_offer_no_path(tmp_path: Path) -> None:
    for source in (
        serve(fake(), toolsets=["versions"], stdio=True),
        serve(fake(), toolsets=["versions"], upload_dirs=[str(tmp_path)]),
    ):
        assert "path" not in cast("Json", (await schema(source))["properties"])


async def test_stdio_with_an_allowlist_offers_content_or_a_path(tmp_path: Path) -> None:
    description = await schema(local(tmp_path))
    properties = cast("dict[str, Json]", description["properties"])

    assert {"path", "content_base64", "filename"} <= set(properties)
    assert description["required"] == ["document_id"]
    assert properties["path"]["description"]


async def test_content_becomes_a_version_and_the_result_names_task_and_version() -> None:
    source = fake()

    output = await call(
        source,
        "upload_document_version",
        document_id=1,
        content_base64=b64(TEXT),
        filename="v2.txt",
        label="second",
        wait_seconds=30,
    )

    [task] = source.state.tasks
    assert output["task_id"] == task.task_id
    assert (output["state"], output["wait"], output["document_id"]) == ("success", "finished", 1)
    assert output["version_id"] == task.document_ids[0]
    assert output["web_url"] == "http://paperless.test/documents/1/details"
    [newest, _] = source.state.documents[0].versions
    assert (newest.id, newest.version_label) == (task.document_ids[0], "second")


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"content_base64": "not base64!", "filename": "x.pdf"}, "not valid base64"),
        ({"content_base64": b64(b"\x7fELF" + bytes(64)), "filename": "x.pdf"}, "can consume"),
        ({"content_base64": b64(TEXT), "filename": ".."}, "file name"),
        ({"content_base64": b64(TEXT), "filename": "x.txt", "label": "x" * 65}, "64 characters"),
    ],
)  # fmt: skip
async def test_a_refusal_reaches_the_client_with_its_message_and_hint(
    arguments: Json, message: str
) -> None:
    source = fake()

    text = await refusal(source, "upload_document_version", document_id=1, **arguments)

    assert message in text
    assert "Hint:" in text
    assert source.state.tasks == []


async def test_a_file_over_the_limit_is_refused() -> None:
    async with Client(server(fake(), max_upload_bytes=64)) as client:
        result = await client.call_tool(
            "upload_document_version",
            {"document_id": 1, "content_base64": b64(bytes(65)), "filename": "x.txt"},
            raise_on_error=False,
        )

    assert result.is_error
    assert "larger than the limit of 64 bytes" in str(result.content)


async def test_a_wait_below_zero_is_refused_by_the_schema() -> None:
    text = await refusal(
        fake(),
        "upload_document_version",
        document_id=1,
        content_base64=b64(TEXT),
        filename="x.txt",
        wait_seconds=-1,
    )

    assert "wait_seconds" in text


async def test_a_path_inside_the_allowlist_is_uploaded_as_a_version(tmp_path: Path) -> None:
    _ = (tmp_path / "scan März.txt").write_bytes(TEXT)
    source = fake()

    async with Client(local(tmp_path, source)) as client:
        result = await client.call_tool(
            "upload_document_version",
            {"document_id": 1, "path": str(tmp_path / "scan März.txt"), "label": "disk"},
        )

    assert result.structured_content is not None
    assert result.structured_content["filename"] == "scan März.txt"
    assert source.state.documents[0].versions[0].version_label == "disk"
    assert source.state.documents[0].original == TEXT


async def test_content_still_works_with_an_allowlist(tmp_path: Path) -> None:
    source = fake()

    async with Client(local(tmp_path, source)) as client:
        result = await client.call_tool(
            "upload_document_version",
            {"document_id": 1, "content_base64": b64(TEXT), "filename": "x.txt"},
        )

    assert result.structured_content is not None
    assert result.structured_content["filename"] == "x.txt"


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
    source = fake()

    async with Client(local(tmp_path, source)) as client:
        result = await client.call_tool(
            "upload_document_version", {"document_id": 1, **arguments}, raise_on_error=False
        )

    assert result.is_error
    assert "Hint:" in str(result.content)
    assert source.state.tasks == []


@pytest.mark.parametrize("target", ["/etc/passwd", "../forbidden.txt", "missing.txt"])
async def test_a_path_outside_the_allowlist_is_refused(tmp_path: Path, target: str) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    _ = (tmp_path / "forbidden.txt").write_bytes(b"forbidden")
    source = fake()
    path = target if target.startswith("/") else f"{allowed}/{target}"

    async with Client(local(allowed, source)) as client:
        result = await client.call_tool(
            "upload_document_version", {"document_id": 1, "path": path}, raise_on_error=False
        )

    assert result.is_error
    assert source.state.tasks == []
    assert "forbidden" not in str(result.content)
