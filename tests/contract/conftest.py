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

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, cast

import httpx2
import pytest
from e2e.helpers import EXPECTED_VERSION, consume, pdf
from fakes.accounts import ADMIN, BLIND, READER
from fakes.paperless import VERSION, FakePaperless, FakeState, FakeTask, seeded

from paperless_mcp.domain.ports import (  # noqa: TC001 -- Ports' annotations are introspected
    DocumentReader,
    InstanceReader,
    TaskReader,
    TaxonomyReader,
)
from paperless_mcp.paperless.auth import StaticTokenProvider
from paperless_mcp.paperless.client import PaperlessClient, RetryPolicy
from paperless_mcp.paperless.documents import PaperlessDocumentReader
from paperless_mcp.paperless.instance import PaperlessInstanceReader
from paperless_mcp.paperless.tasks import PaperlessTaskReader
from paperless_mcp.paperless.taxonomy import PaperlessTaxonomyReader

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from e2e.helpers import Stack


@dataclass(frozen=True)
class Ports:
    """One credential's implementation of every port, one field per port."""

    instance: InstanceReader
    taxonomy: TaxonomyReader
    documents: DocumentReader
    tasks: TaskReader


def fake_ports(fake: FakePaperless) -> Ports:
    """The fake implements every port itself."""
    return Ports(instance=fake, taxonomy=fake, documents=fake, tasks=fake)


def live_ports(client: PaperlessClient) -> Ports:
    """The adapter, one object per port, all on `client`."""
    return Ports(
        instance=PaperlessInstanceReader(client),
        taxonomy=PaperlessTaxonomyReader(client),
        documents=PaperlessDocumentReader(client),
        tasks=PaperlessTaskReader(client),
    )


class Consume(Protocol):
    """Runs a consumption task to its end and returns its id."""

    def __call__(self, *, succeed: bool) -> str: ...


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


def fake_backend() -> Backend:
    state = seeded()
    return Backend(
        admin=fake_ports(FakePaperless(state, user=ADMIN[0])),
        reader=fake_ports(FakePaperless(state, user=READER[0])),
        blind=fake_ports(FakePaperless(state, user=BLIND[0])),
        consume=fake_consume(state),
        expected_version=VERSION,
    )


@pytest.fixture(params=["fake", pytest.param("live", marks=pytest.mark.e2e)])
async def backend(request: pytest.FixtureRequest) -> AsyncIterator[Backend]:
    """The backend under contract (the fixture value is created fresh for every test)."""
    if cast("str", request.param) == "fake":
        yield fake_backend()
        return
    stack = cast("Stack", request.getfixturevalue("stack"))
    created: list[int] = []

    def consume_live(*, succeed: bool) -> str:
        title = f"contract-task-{uuid.uuid4().hex}"
        content = pdf(title) if succeed else b"%PDF-1.4 broken"
        task_id, document = consume(stack.admin, title, content)
        if document is not None:
            created.append(document)
        return task_id

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
                expected_version=EXPECTED_VERSION,
            )
    finally:
        for document in created:
            _ = stack.admin.delete(f"/api/documents/{document}/")
