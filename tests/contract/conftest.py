"""The backends the contract suite runs against: FakePaperless and the adapter on live Paperless.

`just check` runs the `fake` backend; `just e2e` runs `live` (marker `e2e`) against the seeded
3.4.2 stack. Both hold the same seed (tests/e2e/helpers.py `seed` and its `SEEDED_OBJECTS`
manifest, mirrored by `fakes.paperless.seeded`), so every contract test makes the same
assertions against both.

Isolation on the live stack, which persists between runs and is shared by every E2E test:
- Never change or delete a seed object, and never assert exact instance-wide counts; rely on the
  seed manifest as lower bounds, or on objects the test created itself.
- A test that writes creates objects with a name unique to the test run (e.g. with a uuid
  suffix) and deletes them again in a fixture finaliser, also when the test fails.

Adding a port: add a field for it to `Ports` and wire it in `fake_ports` and `live_ports`,
implement it in FakePaperless and the adapter, and write its contract tests with
`@pytest.mark.contract("Port.method")` and the `backend` fixture. test_coverage.py fails until
every port is wired and every method has such a test.
"""

import asyncio
import contextlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, cast

import httpx2
import pytest
from e2e.helpers import EXPECTED_VERSION, consume, pdf, results
from fakes.accounts import ADMIN, BLIND, READER
from fakes.paperless import (
    VERSION,
    FakeDocument,
    FakePaperless,
    FakeState,
    FakeTask,
    seeded,
)

from paperless_mcp.domain.ports import (  # noqa: TC001 -- Ports' annotations are introspected
    AiReader,
    DocumentReader,
    DocumentWriter,
    InstanceReader,
    TaskReader,
    TaxonomyReader,
    TaxonomyWriter,
)
from paperless_mcp.paperless.ai import PaperlessAiReader
from paperless_mcp.paperless.auth import StaticTokenProvider
from paperless_mcp.paperless.client import PaperlessClient, RetryPolicy
from paperless_mcp.paperless.documents import PaperlessDocumentReader, PaperlessDocumentWriter
from paperless_mcp.paperless.instance import PaperlessInstanceReader
from paperless_mcp.paperless.tasks import PaperlessTaskReader
from paperless_mcp.paperless.taxonomy import PaperlessTaxonomyReader, PaperlessTaxonomyWriter

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Callable

    from e2e.helpers import Stack


# Every tag a contract test creates starts with this; the live backend deletes them again.
SCRATCH_TAG = "contract-tag-"


@dataclass(frozen=True)
class Ports:
    """One credential's implementation of every port, one field per port."""

    instance: InstanceReader
    taxonomy: TaxonomyReader
    documents: DocumentReader
    tasks: TaskReader
    document_writes: DocumentWriter
    taxonomy_writes: TaxonomyWriter
    ai: AiReader


def fake_ports(state: FakeState, user: str) -> Ports:
    """The fake implements every port itself; only its writer ports may write."""
    fake = FakePaperless(state, user=user)
    writer = FakePaperless(state, user=user, allow_writes=True)
    return Ports(
        instance=fake,
        taxonomy=fake,
        documents=fake,
        tasks=fake,
        document_writes=writer,
        taxonomy_writes=writer,
        ai=fake,
    )


def live_ports(client: PaperlessClient) -> Ports:
    """The adapter, one object per port, all on `client`."""
    return Ports(
        instance=PaperlessInstanceReader(client),
        taxonomy=PaperlessTaxonomyReader(client),
        documents=PaperlessDocumentReader(client),
        tasks=PaperlessTaskReader(client),
        document_writes=PaperlessDocumentWriter(client),
        taxonomy_writes=PaperlessTaxonomyWriter(client),
        ai=PaperlessAiReader(client),
    )


class Consume(Protocol):
    """Runs a consumption task to its end and returns its id."""

    def __call__(self, *, succeed: bool) -> str: ...


class Upload(Protocol):
    """Uploads a file as a new document, waits for its consumption and returns the document id."""

    def __call__(self, title: str, filename: str, content: bytes, mime_type: str) -> int: ...


@dataclass(frozen=True)
class Backend:
    """One Paperless, seen through the seeded superuser, restricted user and blind user.

    The blind user may sign in but holds no model permission (tests/fakes/accounts.py).
    """

    admin: Ports
    reader: Ports
    blind: Ports
    # Starts a consumption task and waits for its end; `succeed=False` uploads a broken PDF.
    # The live one uploads for real and deletes the documents again when the test ends.
    consume: Consume
    # Uploads a document; the live one deletes it again when the test ends, after consumption.
    upload: Upload
    # A new document of the admin to write to, after its consumption; the live one is deleted when
    # the test ends. Its tags and custom fields are whatever consumption gave it.
    new_document: Callable[[], int]
    # The Paperless release behind the backend: the fake's, or the one the live stack runs
    # (`just e2e-latest` runs the suite against a newer release than the pinned one).
    expected_version: str


def fake_consume(state: FakeState) -> Consume:
    def consume_fake(*, succeed: bool) -> str:
        now = datetime.now(UTC)
        task = FakeTask(
            id=len(state.tasks) + 1,
            task_id=str(uuid.uuid4()),
            task_type="consume_file",
            status="success" if succeed else "failure",
            created=now,
            finished=now,
            document_ids=(len(state.documents) + 1,) if succeed else (),
            message=None if succeed else "broken.pdf: Error occurred while consuming document",
        )
        state.tasks.append(task)
        return task.task_id

    return consume_fake


def fake_upload(state: FakeState) -> Upload:
    def upload_fake(title: str, filename: str, content: bytes, mime_type: str) -> int:
        number = max((d.id for d in state.documents), default=0) + 1
        state.documents.append(
            FakeDocument(
                id=number,
                title=title,
                mime_type=mime_type,
                file_name=filename,
                original=content,
                archive=content + b"\n%archive" if mime_type == "application/pdf" else None,
            )
        )
        return number

    return upload_fake


def fake_new_document(state: FakeState) -> Callable[[], int]:
    def new_document() -> int:
        [admin] = [user for user in state.users if user.username == ADMIN[0]]
        number = max(d.id for d in state.documents) + 1
        state.documents.append(
            FakeDocument(id=number, title=f"contract-write-{uuid.uuid4().hex}", owner=admin.id)
        )
        return number

    return new_document


def fake_backend(*, ai_enabled: bool = False) -> Backend:
    """The fake's backend; the E2E stack of `just e2e` has AI off, so that is the default."""
    state = seeded()
    state.ai_enabled = ai_enabled
    return Backend(
        admin=fake_ports(state, ADMIN[0]),
        reader=fake_ports(state, READER[0]),
        blind=fake_ports(state, BLIND[0]),
        consume=fake_consume(state),
        upload=fake_upload(state),
        new_document=fake_new_document(state),
        expected_version=VERSION,
    )


@contextlib.asynccontextmanager
async def live_backend(stack: Stack) -> AsyncGenerator[Backend]:
    """The adapter on the live stack; documents a test uploads are deleted when it ends."""
    created: list[int] = []

    def consume_live(*, succeed: bool) -> str:
        title = f"contract-task-{uuid.uuid4().hex}"
        content = pdf(title) if succeed else b"%PDF-1.4 broken"
        task_id, document = consume(stack.admin, title, content)
        if document is not None:
            created.append(document)
        return task_id

    def upload_live(title: str, filename: str, content: bytes, mime_type: str) -> int:
        _, document = consume(stack.admin, title, content, filename=filename, mime_type=mime_type)
        assert document is not None, f"Consuming {title!r} failed"
        created.append(document)
        return document

    def new_live_document() -> int:
        title = f"contract-write-{uuid.uuid4().hex}"
        _, document = consume(stack.admin, title, pdf(title))
        assert document is not None
        created.append(document)
        return document

    try:
        async with httpx2.AsyncClient(base_url=stack.base_url, timeout=30.0) as http:

            def ports(token: str) -> Ports:
                credentials = StaticTokenProvider(token)
                return live_ports(
                    PaperlessClient(http, credentials, retry=RetryPolicy(), upload_timeout=60.0)
                )

            yield Backend(
                admin=ports(stack.admin_token),
                reader=ports(stack.reader_token),
                blind=ports(stack.blind_token),
                consume=consume_live,
                upload=upload_live,
                new_document=new_live_document,
                expected_version=EXPECTED_VERSION,
            )
    finally:
        for document in created:
            _ = stack.admin.delete(f"/api/documents/{document}/")
        # Tags the contract tests created (they all start with SCRATCH_TAG).
        found = stack.admin.get("/api/tags/", params={"name__istartswith": SCRATCH_TAG})
        for row in results(found):
            _ = stack.admin.delete(f"/api/tags/{row['id']}/")


@pytest.fixture(params=["fake", pytest.param("live", marks=pytest.mark.e2e)])
async def backend(request: pytest.FixtureRequest) -> AsyncIterator[Backend]:
    """The backend under contract (the fixture value is created fresh for every test)."""
    if cast("str", request.param) == "fake":
        yield fake_backend()
        return
    async with live_backend(cast("Stack", request.getfixturevalue("stack"))) as live:
        yield live


INDEX_TIMEOUT = 600.0


async def build_llm_index(stack: Stack, live: Backend) -> None:
    """Update the LLM index (the chat answers from it) and wait for the task to end.

    Unverified on a working backend (the CI stack has none): `task_type` is the 3.4.2 name of
    the task, from the OpenAPI schema.
    """
    started = stack.admin.post("/api/tasks/run/", json={"task_type": "llm_index"})
    _ = started.raise_for_status()
    task_id = str(cast("dict[str, object]", started.json())["task_id"])
    deadline = asyncio.get_running_loop().time() + INDEX_TIMEOUT
    while True:
        status = await live.admin.tasks.get_task(task_id)
        if status.is_final:
            break
        if asyncio.get_running_loop().time() > deadline:
            pytest.fail(f"The LLM index update did not end within {INDEX_TIMEOUT:.0f} s.")
        await asyncio.sleep(3)
    if status.state != "success":
        pytest.fail(f"The LLM index update ended {status.state}: {status.message}")


AI_SKIPPED = "AI is disabled on this Paperless; `just e2e-ai` runs these against an AI backend."


@pytest.fixture(params=["fake", pytest.param("live", marks=[pytest.mark.e2e, pytest.mark.ai_e2e])])
async def ai_backend(request: pytest.FixtureRequest) -> AsyncIterator[Backend]:
    """A backend with Paperless AI on: the fake with AI enabled, or a live one that has it.

    The live stack of CI has AI off, so those tests skip there; `just e2e-ai` starts a stack
    with an AI backend the developer provides and runs them.
    """
    if cast("str", request.param) == "fake":
        yield fake_backend(ai_enabled=True)
        return
    stack = cast("Stack", request.getfixturevalue("stack"))
    async with live_backend(stack) as live:
        if (await live.admin.instance.get_overview()).ai_enabled is not True:
            pytest.skip(AI_SKIPPED)
        await build_llm_index(stack, live)
        yield live
