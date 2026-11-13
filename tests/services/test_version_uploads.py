"""VersionUploadService: a file becomes a new version of a document, with upload_document's checks.

Nothing is sent before everything is checked: a bad file, name, label or document leaves
Paperless untouched (`state.tasks` stays empty), and the file's type comes from its bytes.
"""

import base64
from dataclasses import replace
from typing import TYPE_CHECKING, override

import pytest
from e2e.helpers import pdf
from fakes.accounts import ADMIN, READER
from fakes.paperless import FakePaperless, FakeState, seeded

from paperless_mcp.domain.errors import (
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    UpstreamTimeout,
    Validation,
)
from paperless_mcp.services.tasks import TaskService
from paperless_mcp.services.version_uploads import VersionUploadService

if TYPE_CHECKING:
    from pathlib import Path

    from paperless_mcp.domain.tasks import TaskStatus
    from paperless_mcp.domain.versions import VersionUpload, VersionUploadResult

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-7")]

WEB = "https://paperless.example"
LIMIT = 10_000
PUBLIC = 1


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


class Recording(FakePaperless):
    """The fake, remembering what it was asked to upload, and slow to finish if told so."""

    def __init__(self, state: FakeState, *, user: str, slow_reads: int = 0) -> None:
        super().__init__(state, user=user, allow_writes=True)
        self.uploads: list[tuple[int, VersionUpload]] = []
        self.slow_reads: int = slow_reads
        self.reads: int = 0
        self.read_error: Exception | None = None
        self.hide_documents: bool = False
        self.fail_with: str | None = None

    @override
    async def upload_document_version(self, document_id: int, upload: VersionUpload) -> str:
        self.uploads.append((document_id, upload))
        task_id = await super().upload_document_version(document_id, upload)
        if self.fail_with:
            [task] = [t for t in self.state.tasks if t.task_id == task_id]
            task.status, task.message, task.document_ids = "failure", self.fail_with, ()
        return task_id

    @override
    async def get_task(self, task_id: str) -> TaskStatus:
        self.reads += 1
        if self.read_error:
            raise self.read_error
        status = await super().get_task(task_id)
        if self.hide_documents:
            return status.model_copy(update={"document_ids": ()})
        if self.reads <= self.slow_reads:
            return status.model_copy(
                update={"state": "started", "document_ids": (), "finished": None}
            )
        return status


class Clock:
    def __init__(self) -> None:
        self.now: float = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.now += delay


class Setup:
    def __init__(
        self,
        user: str = ADMIN[0],
        *,
        permissions: frozenset[str] | None = None,
        slow_reads: int = 0,
        upload_dirs: tuple[str, ...] = (),
    ) -> None:
        self.state: FakeState = seeded()
        if permissions is not None:
            self.state.users = [
                replace(u, is_superuser=False, permissions=permissions) if u.username == user else u
                for u in self.state.users
            ]
        self.fake: Recording = Recording(self.state, user=user, slow_reads=slow_reads)
        self.clock: Clock = Clock()
        self.service: VersionUploadService = VersionUploadService(
            self.fake,
            self.fake,
            TaskService(self.fake, sleep=self.clock.sleep, clock=self.clock),
            web_url=WEB,
            max_bytes=LIMIT,
            upload_dirs=upload_dirs,
        )

    async def upload(
        self,
        content: bytes = b"",
        filename: str = "v2.pdf",
        *,
        document: int = PUBLIC,
        label: str | None = None,
        wait_seconds: float = 0,
    ) -> VersionUploadResult:
        return await self.service.from_content(
            document,
            b64(content or pdf("hello")),
            filename,
            label,
            wait_seconds=wait_seconds,
        )

    @property
    def sent(self) -> tuple[int, VersionUpload]:
        [upload] = self.fake.uploads
        return upload

    def untouched(self) -> bool:
        return not self.fake.uploads and not self.state.tasks


# --- what is uploaded ---------------------------------------------------------------------------


async def test_an_upload_returns_the_task_the_document_and_the_new_version() -> None:
    setup = Setup()

    result = await setup.upload(label="final")

    [task] = setup.state.tasks
    assert result.task_id == task.task_id
    assert (result.state, result.message, result.wait, result.waited_seconds) == (
        "success",
        None,
        "not_requested",
        0,
    )
    [version] = task.document_ids
    assert (result.document_id, result.version_id) == (PUBLIC, version)
    assert result.web_url == f"{WEB}/documents/1/details"
    assert (result.filename, result.mime_type) == ("v2.pdf", "application/pdf")
    document, upload = setup.sent
    assert result.size_bytes == len(upload.content)
    assert (document, upload.content, upload.label) == (PUBLIC, pdf("hello"), "final")
    assert [v.id for v in setup.state.documents[0].versions] == [version, PUBLIC]


@pytest.mark.parametrize(("label", "sent"), [(None, None), ("", None), ("  ", None), (" a ", "a")])
async def test_the_label_is_trimmed_and_a_blank_one_is_left_out(
    label: str | None, sent: str | None
) -> None:
    setup = Setup()

    _ = await setup.upload(label=label)

    assert setup.sent[1].label == sent


async def test_a_label_of_64_characters_fits_and_65_are_refused_before_anything_is_sent() -> None:
    setup = Setup()

    _ = await setup.upload(label="x" * 64)
    other = Setup()
    with pytest.raises(Validation, match="at most 64 characters; this one has 65"):
        _ = await other.upload(label="x" * 65)

    assert setup.sent[1].label == "x" * 64
    assert other.untouched()


# --- the file ----------------------------------------------------------------------------------


async def test_the_type_comes_from_the_bytes_not_from_the_name() -> None:
    setup = Setup()

    result = await setup.upload(pdf("x"), "notes.txt")
    again = await setup.upload(b"a,b\n1,2\n", "table.CSV")

    assert [u.mime_type for _, u in setup.fake.uploads] == ["application/pdf", "text/csv"]
    assert (result.mime_type, again.mime_type) == ("application/pdf", "text/csv")


@pytest.mark.parametrize(
    "content",
    [b"\x7fELF\x02\x01\x01" + bytes(64), b"<html></html>", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"],
)
async def test_a_file_paperless_cannot_consume_is_refused_before_anything_is_sent(
    content: bytes,
) -> None:
    setup = Setup()

    with pytest.raises(Validation) as raised:
        _ = await setup.upload(content, "v2.pdf")

    assert raised.value.message == "The file is not a type Paperless can consume."
    assert "PDF" in (raised.value.hint or "")
    assert setup.untouched()


async def test_the_file_name_is_cleaned_and_non_ascii_names_are_kept() -> None:
    setup = Setup()

    _ = await setup.upload(filename="../../tmp/Rechnung Käse 日本語.pdf")
    _ = await setup.upload(pdf("two"), "C:\\Scans\\März.pdf")

    assert [u.filename for _, u in setup.fake.uploads] == ["Rechnung Käse 日本語.pdf", "März.pdf"]


async def test_a_name_that_is_no_name_is_refused_before_anything_is_sent() -> None:
    setup = Setup()

    with pytest.raises(Validation, match="file name"):
        _ = await setup.upload(filename="..")

    assert setup.untouched()


async def test_a_file_over_the_limit_or_not_base64_is_refused_before_anything_is_sent() -> None:
    setup = Setup()

    with pytest.raises(Validation, match="larger than the limit of 10000 bytes"):
        _ = await setup.upload(pdf("x") + bytes(LIMIT))
    with pytest.raises(Validation, match="base64"):
        _ = await setup.service.from_content(PUBLIC, "not base64!", "a.pdf", None)

    assert setup.untouched()


async def test_a_file_exactly_at_the_limit_is_accepted() -> None:
    setup = Setup()
    content = b"x" * LIMIT

    _ = await setup.upload(content, "big.txt")

    assert len(setup.sent[1].content) == LIMIT


# --- the document, permissions and the outcome ---------------------------------------------


async def test_a_missing_or_hidden_document_is_not_found_and_nothing_is_sent() -> None:
    setup = Setup()
    reader = Setup(READER[0])

    with pytest.raises(NotFound):
        _ = await setup.upload(document=999)
    with pytest.raises(NotFound):
        _ = await reader.upload(document=2)  # the private document

    assert setup.untouched()
    assert reader.untouched()


async def test_a_version_id_is_not_a_document_and_the_hint_says_so() -> None:
    setup = Setup()
    version = (await setup.upload()).version_id
    assert version is not None
    other = Setup()

    with pytest.raises(NotFound):
        _ = await other.upload(document=version)

    assert other.untouched()


async def test_a_user_who_may_not_change_documents_is_denied() -> None:
    setup = Setup(user=READER[0])

    with pytest.raises(PermissionDenied):
        _ = await setup.upload()

    assert setup.state.tasks == []


async def test_a_user_who_may_not_view_tasks_still_gets_the_task_id() -> None:
    setup = Setup(
        user=READER[0],
        permissions=frozenset(
            {"view_document", "add_document", "change_document", "view_uisettings"}
        ),
    )
    setup.state.documents[0].owner = None

    result = await setup.upload(wait_seconds=30)

    assert result.task_id == setup.state.tasks[0].task_id
    assert (result.state, result.version_id, result.wait) == ("unknown", None, "timed_out")
    assert result.document_id == PUBLIC
    assert result.web_url == f"{WEB}/documents/1/details"
    assert result.waited_seconds == 0
    assert result.message is not None
    assert "outcome is unknown" in result.message
    assert "get_task" in result.message


async def test_an_unreadable_task_without_a_wait_reports_no_wait() -> None:
    setup = Setup(
        user=READER[0],
        permissions=frozenset(
            {"view_document", "add_document", "change_document", "view_uisettings"}
        ),
    )
    setup.state.documents[0].owner = None

    result = await setup.upload()

    assert (result.state, result.wait) == ("unknown", "not_requested")


async def test_a_success_without_a_version_still_returns_the_task() -> None:
    setup = Setup()
    setup.fake.hide_documents = True

    result = await setup.upload(wait_seconds=30)

    assert result.task_id == setup.state.tasks[0].task_id
    assert (result.state, result.version_id) == ("success", None)
    assert result.message is not None
    assert "no version" in result.message


@pytest.mark.parametrize("error", [NotFound("gone"), UpstreamTimeout("slow")])
async def test_a_task_that_cannot_be_read_after_the_upload_never_hides_its_id(
    error: PaperlessMcpError,
) -> None:
    """The file is in Paperless by then: an error here would invite a retry and a second version."""
    setup = Setup()
    setup.fake.read_error = error

    result = await setup.upload(wait_seconds=30)

    assert result.task_id == setup.state.tasks[0].task_id
    assert (result.state, result.version_id) == ("unknown", None)
    assert result.message is not None
    assert error.message in result.message


async def test_any_failure_reading_the_task_keeps_the_id_and_hides_the_detail() -> None:
    setup = Setup()
    setup.fake.read_error = RuntimeError("secret internals")

    result = await setup.upload(wait_seconds=30)

    assert result.task_id == setup.state.tasks[0].task_id
    assert result.state == "unknown"
    assert result.message is not None
    assert "secret internals" not in result.message


async def test_a_wait_returns_the_version_once_consumption_ends() -> None:
    setup = Setup(slow_reads=2)

    result = await setup.upload(wait_seconds=30)

    assert (result.state, result.wait) == ("success", "finished")
    assert result.version_id == setup.state.tasks[0].document_ids[0]
    assert result.waited_seconds == 4.0  # two polls of two seconds on the injected clock


async def test_a_wait_that_runs_out_reports_the_task_still_running() -> None:
    setup = Setup(slow_reads=10_000)

    result = await setup.upload(wait_seconds=5)

    assert (result.state, result.wait, result.version_id) == ("started", "timed_out", None)
    assert result.waited_seconds == 5.0


async def test_a_failed_consumption_is_reported_with_paperless_reason() -> None:
    setup = Setup()
    setup.fake.fail_with = "v2.pdf: Not consuming v2.pdf: broken"

    result = await setup.upload(wait_seconds=5)

    assert (result.state, result.version_id, result.message) == (
        "failure",
        None,
        "v2.pdf: Not consuming v2.pdf: broken",
    )
    assert result.web_url == f"{WEB}/documents/1/details"


# --- by path ---------------------------------------------------------------------------------


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    root = tmp_path / "inbox"
    root.mkdir()
    _ = (root / "scan März.pdf").write_bytes(pdf("from disk"))
    _ = (root / "notes.bin").write_bytes(b"\x7fELF" + bytes(64))
    return root


async def test_a_file_in_an_allowed_directory_is_uploaded_under_its_own_name(folder: Path) -> None:
    setup = Setup(upload_dirs=(str(folder),))

    result = await setup.service.from_path(PUBLIC, str(folder / "scan März.pdf"), None, "disk")

    document, upload = setup.sent
    assert (document, upload.filename, upload.content, upload.label) == (
        PUBLIC,
        "scan März.pdf",
        pdf("from disk"),
        "disk",
    )
    assert result.filename == "scan März.pdf"


async def test_a_name_given_with_the_path_wins_and_an_empty_one_is_refused(folder: Path) -> None:
    setup = Setup(upload_dirs=(str(folder),))

    _ = await setup.service.from_path(PUBLIC, str(folder / "scan März.pdf"), "../renamed.pdf", None)
    with pytest.raises(Validation, match="file name"):
        _ = await setup.service.from_path(PUBLIC, str(folder / "scan März.pdf"), "", None)

    assert [u.filename for _, u in setup.fake.uploads] == ["renamed.pdf"]


async def test_a_path_outside_the_allowlist_sends_nothing(folder: Path) -> None:
    setup = Setup(upload_dirs=(str(folder),))

    with pytest.raises(NotFound):
        _ = await setup.service.from_path(PUBLIC, "/etc/passwd", None, None)
    with pytest.raises(NotFound):
        _ = await setup.service.from_path(PUBLIC, f"{folder}/../../etc/passwd", None, None)

    assert setup.untouched()


async def test_without_an_allowlist_a_path_is_refused(folder: Path) -> None:
    setup = Setup()

    with pytest.raises(Validation, match="not enabled"):
        _ = await setup.service.from_path(PUBLIC, str(folder / "scan März.pdf"), None, None)

    assert setup.untouched()


async def test_a_file_from_disk_gets_the_same_checks_as_content(folder: Path) -> None:
    setup = Setup(upload_dirs=(str(folder),))

    with pytest.raises(Validation, match="not a type Paperless can consume"):
        _ = await setup.service.from_path(PUBLIC, str(folder / "notes.bin"), None, None)
    with pytest.raises(Validation, match="at most 64 characters"):
        _ = await setup.service.from_path(PUBLIC, str(folder / "scan März.pdf"), None, "x" * 65)

    assert setup.untouched()


async def test_no_wait_is_the_default_for_both_sources(folder: Path) -> None:
    setup = Setup(upload_dirs=(str(folder),))

    one = await setup.service.from_content(PUBLIC, b64(pdf("x")), "a.pdf", None)
    two = await setup.service.from_path(PUBLIC, str(folder / "scan März.pdf"), None, None)

    assert (one.wait, two.wait) == ("not_requested", "not_requested")
    assert setup.clock.now == 0
