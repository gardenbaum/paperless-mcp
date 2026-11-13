"""The exact words of the errors an upload gives: they are what an LLM acts on.

One recorded snapshot, so a changed message or hint is a deliberate change that shows up in
review (and a mutated literal cannot go unnoticed).
"""

from typing import TYPE_CHECKING

import pytest
from e2e.helpers import pdf

from paperless_mcp.domain.documents import UploadMetadata
from paperless_mcp.domain.errors import PaperlessMcpError
from paperless_mcp.services.upload_source import clean_filename, decode_base64, read_allowed
from services.test_uploads import Setup, b64

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from syrupy.assertion import SnapshotAssertion

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-7")]


def words(call: Callable[[], object]) -> str:
    with pytest.raises(PaperlessMcpError) as raised:
        _ = call()
    return f"{type(raised.value).__name__}: {raised.value.describe()}"


async def awords(call: Callable[[], Awaitable[object]]) -> str:
    with pytest.raises(PaperlessMcpError) as raised:
        _ = await call()
    return f"{type(raised.value).__name__}: {raised.value.describe()}"


def test_the_words_of_the_source_checks(snapshot: SnapshotAssertion, tmp_path: Path) -> None:
    root = tmp_path / "in"
    root.mkdir()
    _ = (root / "empty").write_bytes(b"")
    _ = (root / "big").write_bytes(bytes(10))
    given = str(root / "empty")
    roots = ["/srv/in", "/srv/more"]

    def read(path: str, **kwargs: object) -> object:
        return read_allowed(path, [str(root)], max_bytes=5, **kwargs)  # pyright: ignore[reportArgumentType] -- keywords

    shown = {
        "too large": words(lambda: decode_base64(b64(bytes(10)), max_bytes=5)),
        "too long to look at": words(lambda: decode_base64("!" * 100, max_bytes=5)),
        "not base64": words(lambda: decode_base64("!!!!", max_bytes=5)),
        "empty content": words(lambda: decode_base64("", max_bytes=5)),
        "no name": words(lambda: clean_filename("..")),
        "control characters": words(lambda: clean_filename("a\x07")),
        "name too long": words(lambda: clean_filename("a" * 300)),
        "paths off": words(lambda: read_allowed(given, [], max_bytes=5)),
        "nul": words(lambda: read("/a\x00b")),
        "relative": words(lambda: read("a/b")),
        "platform": words(lambda: read(given, secure=False)),
        "outside": words(lambda: read_allowed("/etc/passwd", roots, max_bytes=5)),
        "empty file": words(lambda: read(given)),
        "file too large": words(lambda: read(str(root / "big"))),
    }

    assert shown == snapshot


async def test_the_words_of_the_upload_checks(snapshot: SnapshotAssertion) -> None:
    setup = Setup()

    async def attempt(content: bytes = b"", **metadata: object) -> object:
        return await setup.service.from_content(
            b64(content or pdf("x")), "a.pdf", UploadMetadata.model_validate(metadata)
        )

    shown = {
        "unsupported type": await awords(lambda: attempt(b"\x7fELF" + bytes(64))),
        "blank title": await awords(lambda: attempt(title=" ")),
        "long title": await awords(lambda: attempt(title="x" * 129)),
        "serial number": await awords(lambda: attempt(archive_serial_number=-1)),
    }

    assert shown == snapshot


async def test_the_words_of_an_outcome_that_is_not_a_plain_success(
    snapshot: SnapshotAssertion,
) -> None:
    hidden, broken = Setup(), Setup()
    hidden.fake.hide_documents = True
    broken.fake.read_error = RuntimeError("secret")

    shown = {
        "no document": (await hidden.upload()).message,
        "unreadable task": (await broken.upload()).message,
    }

    assert shown == snapshot
