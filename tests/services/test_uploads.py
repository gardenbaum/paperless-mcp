"""UploadService: a file and metadata by name become one upload to Paperless, checked first.

Nothing is sent before everything is checked: a bad name, value, file type or size leaves
Paperless untouched (`state.tasks` stays empty), and the file's type comes from its bytes.
"""

import base64
from dataclasses import replace
from datetime import date
from typing import TYPE_CHECKING, override

import pytest
from e2e.helpers import pdf
from fakes.accounts import ADMIN, READER
from fakes.paperless import FakePaperless, FakeState, seeded

from paperless_mcp.domain.documents import DocumentUpload, UploadMetadata
from paperless_mcp.domain.errors import (
    Ambiguous,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    UpstreamTimeout,
    Validation,
)
from paperless_mcp.paperless.auth import StaticTokenProvider
from paperless_mcp.services.tasks import TaskService
from paperless_mcp.services.taxonomy import TaxonomyResolver
from paperless_mcp.services.uploads import UploadService

if TYPE_CHECKING:
    from pathlib import Path

    from paperless_mcp.domain.documents import UploadResult
    from paperless_mcp.domain.tasks import TaskStatus

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-7")]

WEB = "https://paperless.example"
LIMIT = 10_000


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


class Recording(FakePaperless):
    """The fake, remembering what it was asked to upload, and slow to finish if told so."""

    def __init__(self, state: FakeState, *, user: str, slow_reads: int = 0) -> None:
        super().__init__(state, user=user, allow_writes=True)
        self.uploads: list[DocumentUpload] = []
        self.slow_reads: int = slow_reads
        self.reads: int = 0
        self.read_error: Exception | None = None
        self.hide_documents: bool = False

    @override
    async def upload_document(self, upload: DocumentUpload) -> str:
        self.uploads.append(upload)
        return await super().upload_document(upload)

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
        taxonomy = TaxonomyResolver(self.fake, StaticTokenProvider("t"), ttl=3600.0)
        self.service: UploadService = UploadService(
            self.fake,
            self.fake,
            taxonomy,
            TaskService(self.fake, sleep=self.clock.sleep, clock=self.clock),
            web_url=WEB,
            max_bytes=LIMIT,
            upload_dirs=upload_dirs,
        )

    async def upload(
        self,
        content: bytes = b"",
        filename: str = "invoice.pdf",
        *,
        wait_seconds: float = 0,
        **metadata: object,
    ) -> UploadResult:
        return await self.service.from_content(
            b64(content or pdf("hello")),
            filename,
            UploadMetadata.model_validate(metadata),
            wait_seconds=wait_seconds,
        )

    def field(self, name: str) -> int:
        [number] = [f.id for f in self.state.custom_fields if f.name == name]
        return number

    @property
    def sent(self) -> DocumentUpload:
        [upload] = self.fake.uploads
        return upload

    def untouched(self) -> bool:
        return not self.fake.uploads and not self.state.tasks


# --- what is uploaded ---------------------------------------------------------------------------


async def test_an_upload_returns_the_task_and_the_new_document() -> None:
    setup = Setup()

    result = await setup.upload(title="Invoice")

    [task] = setup.state.tasks
    assert result.task_id == task.task_id
    assert (result.state, result.message, result.wait, result.waited_seconds) == (
        "success",
        None,
        "not_requested",
        0,
    )
    [document] = task.document_ids
    assert result.document_id == document
    assert result.web_url == f"{WEB}/documents/{document}/details"
    assert (result.filename, result.mime_type) == ("invoice.pdf", "application/pdf")
    assert result.size_bytes == len(setup.sent.content)
    assert setup.sent.content == pdf("hello")


async def test_names_become_ids_and_custom_field_values_take_their_stored_form() -> None:
    setup = Setup()

    _ = await setup.upload(
        title="  Invoice März  ",
        created=date(2026, 2, 3),
        archive_serial_number=0,
        correspondent="E2E-ACME",
        document_type=1,
        storage_path="e2e-archive",
        tags=["e2e-invoices", "E2E-INBOX", "e2e-invoices"],
        custom_fields={
            "e2e-select": "beta",
            "e2e-monetary": "USD 7.5",
            str(setup.field("e2e-integer")): 42,
            "e2e-documentlink": [1, 1],
        },
    )

    upload = setup.sent
    assert upload.title == "Invoice März"
    assert (upload.created, upload.archive_serial_number) == (date(2026, 2, 3), 0)
    assert (upload.correspondent, upload.document_type, upload.storage_path) == (1, 1, 1)
    assert upload.tags == (2, 4)
    values = {item.field: item.value for item in upload.custom_fields}
    assert values == {
        setup.field("e2e-select"): "opt-beta",
        setup.field("e2e-monetary"): "USD7.50",
        setup.field("e2e-integer"): 42,
        setup.field("e2e-documentlink"): [1],
    }


async def test_what_is_left_out_is_left_to_paperless() -> None:
    setup = Setup()

    _ = await setup.upload()

    upload = setup.sent
    assert (upload.title, upload.created, upload.archive_serial_number) == (None, None, None)
    assert (upload.correspondent, upload.document_type, upload.storage_path) == (None, None, None)
    assert (upload.tags, upload.custom_fields) == ((), ())


# --- the file ----------------------------------------------------------------------------------


async def test_the_type_comes_from_the_bytes_not_from_the_name() -> None:
    setup = Setup()

    result = await setup.upload(pdf("x"), "notes.txt")
    again = await setup.upload(b"a,b\n1,2\n", "table.CSV")

    assert (setup.fake.uploads[0].mime_type, result.mime_type) == ("application/pdf",) * 2
    assert (setup.fake.uploads[1].mime_type, again.mime_type) == ("text/csv",) * 2


@pytest.mark.parametrize(
    "content",
    [b"\x7fELF\x02\x01\x01" + bytes(64), b"<html></html>", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"],
)
async def test_a_file_paperless_cannot_consume_is_refused_before_anything_is_sent(
    content: bytes,
) -> None:
    setup = Setup()

    with pytest.raises(Validation, match="not a type Paperless can consume") as raised:
        _ = await setup.upload(content, "invoice.pdf")

    assert "PDF" in (raised.value.hint or "")
    assert setup.untouched()


async def test_the_file_name_is_cleaned_and_non_ascii_names_are_kept() -> None:
    setup = Setup()

    _ = await setup.upload(filename="../../tmp/Rechnung Käse 日本語.pdf")
    _ = await setup.upload(pdf("two"), "C:\\Scans\\März.pdf")

    assert [u.filename for u in setup.fake.uploads] == ["Rechnung Käse 日本語.pdf", "März.pdf"]


async def test_a_title_of_exactly_128_characters_is_accepted() -> None:
    setup = Setup()

    _ = await setup.upload(title="x" * 128)

    assert setup.sent.title == "x" * 128


async def test_no_wait_is_the_default_for_both_sources(folder: Path) -> None:
    setup = Setup(upload_dirs=(str(folder),))
    content = b64(pdf("x"))

    one = await setup.service.from_content(content, "a.pdf", UploadMetadata())
    two = await setup.service.from_path(str(folder / "scan März.pdf"), None, UploadMetadata())

    assert (one.wait, two.wait) == ("not_requested", "not_requested")
    assert setup.clock.now == 0


async def test_a_wait_of_one_second_that_cannot_be_made_is_reported_as_timed_out() -> None:
    setup = Setup(user=READER[0], permissions=frozenset({"add_document", "view_uisettings"}))

    result = await setup.upload(wait_seconds=1)

    assert result.wait == "timed_out"


async def test_the_largest_archive_serial_number_is_accepted() -> None:
    setup = Setup()

    _ = await setup.upload(archive_serial_number=4_294_967_295)

    assert setup.sent.archive_serial_number == 4_294_967_295


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
        _ = await setup.service.from_content("not base64!", "a.pdf", UploadMetadata())

    assert setup.untouched()


# --- names and values are checked before anything is sent --------------------------------------


@pytest.mark.parametrize(
    ("metadata", "error", "message"),
    [
        ({"tags": ["nope"]}, NotFound, "No tag is named 'nope'"),
        ({"tags": ["e2e-invoices", 999]}, NotFound, "No tag has id 999"),
        ({"correspondent": "nobody"}, NotFound, "No correspondent is named"),
        ({"document_type": "nope"}, NotFound, "No document type is named"),
        ({"storage_path": 99}, NotFound, "No storage path has id 99"),
        ({"custom_fields": {"nope": 1}}, NotFound, "No custom field is named"),
        ({"custom_fields": {"e2e-integer": "abc"}}, Validation, "whole number"),
        ({"custom_fields": {"e2e-select": "gamma"}}, Validation, "no option"),
        ({"custom_fields": {"e2e-integer": 1, "5": 3}}, Validation, "given twice"),
        ({"custom_fields": {"e2e-documentlink": [2]}}, NotFound, "No document 2 exists"),
        ({"title": "   "}, Validation, "title cannot be blank"),
        ({"title": "x" * 129}, Validation, "longer than 128"),
        ({"archive_serial_number": -1}, Validation, "archive serial number"),
        ({"archive_serial_number": 4_294_967_296}, Validation, "archive serial number"),
    ],
)
async def test_a_bad_name_or_value_is_refused_before_anything_is_sent(
    metadata: dict[str, object], error: type[Exception], message: str
) -> None:
    setup = Setup(
        user=READER[0],
        permissions=frozenset(
            {
                "view_document",
                "view_tag",
                "view_correspondent",
                "view_documenttype",
                "view_storagepath",
                "view_customfield",
                "add_document",
                "view_paperlesstask",
            }
        ),
    )
    setup.state.documents = [d for d in setup.state.documents if d.id == 1]  # 2 is private

    with pytest.raises(error, match=message):
        _ = await setup.service.from_content(
            b64(pdf("hello")), "invoice.pdf", UploadMetadata.model_validate(metadata)
        )

    assert setup.untouched()


async def test_an_ambiguous_name_is_refused_with_its_candidates() -> None:
    setup = Setup()
    setup.state.correspondents.append(replace(setup.state.correspondents[0], id=2, name="E2E-Acme"))

    with pytest.raises(Ambiguous, match="2 correspondents match"):
        _ = await setup.upload(correspondent="e2e-ACME")

    assert setup.untouched()


async def test_a_tag_that_does_not_exist_is_never_created_by_an_upload() -> None:
    setup = Setup()
    tags = list(setup.state.tags)

    with pytest.raises(NotFound):
        _ = await setup.upload(tags=["brand-new"])

    assert setup.state.tags == tags


# --- permissions and the outcome --------------------------------------------------------------


async def test_a_user_who_may_not_add_documents_is_denied() -> None:
    setup = Setup(user=READER[0])

    with pytest.raises(PermissionDenied):
        _ = await setup.upload()

    assert setup.state.tasks == []


async def test_a_user_who_may_not_view_tasks_still_gets_the_task_id() -> None:
    setup = Setup(user=READER[0], permissions=frozenset({"add_document", "view_uisettings"}))
    # The task was created anyway; the reader cannot look at it.

    result = await setup.upload(wait_seconds=30)

    assert result.task_id == setup.state.tasks[0].task_id
    assert (result.state, result.document_id, result.web_url, result.wait) == (
        "unknown",
        None,
        None,
        "timed_out",  # a wait was asked for, and could not be made
    )
    assert result.waited_seconds == 0
    assert result.message is not None
    assert "outcome is unknown" in result.message
    assert "get_task" in result.message


async def test_an_unreadable_task_without_a_wait_reports_no_wait() -> None:
    setup = Setup(user=READER[0], permissions=frozenset({"add_document", "view_uisettings"}))

    result = await setup.upload()

    assert (result.state, result.wait) == ("unknown", "not_requested")


async def test_a_success_without_a_document_still_returns_the_task() -> None:
    """Barcode splitting, or a document the user may not see: success, but no id to name."""
    setup = Setup()
    setup.fake.hide_documents = True

    result = await setup.upload(wait_seconds=30)

    assert result.task_id == setup.state.tasks[0].task_id
    assert (result.state, result.document_id, result.web_url) == ("success", None, None)
    assert result.message is not None
    assert "no document" in result.message


async def test_any_failure_reading_the_task_keeps_the_id_and_hides_the_detail() -> None:
    setup = Setup()
    setup.fake.read_error = RuntimeError("secret internals")

    result = await setup.upload(wait_seconds=30)

    assert result.task_id == setup.state.tasks[0].task_id
    assert result.state == "unknown"
    assert result.message is not None
    assert "secret internals" not in result.message


@pytest.mark.parametrize("error", [NotFound("gone"), UpstreamTimeout("slow")])
async def test_a_task_that_cannot_be_read_after_the_upload_never_hides_its_id(
    error: PaperlessMcpError,
) -> None:
    """The file is in Paperless by then: an error here would invite a retry and a duplicate."""
    setup = Setup()
    setup.fake.read_error = error

    result = await setup.upload(wait_seconds=30)

    assert result.task_id == setup.state.tasks[0].task_id
    assert (result.state, result.document_id) == ("unknown", None)
    assert result.message is not None
    assert error.message in result.message


async def test_a_failed_consumption_is_reported_with_paperless_reason() -> None:
    setup = Setup()
    _ = await setup.upload(archive_serial_number=77)

    result = await setup.upload(pdf("other"), archive_serial_number=77)

    assert result.state == "failure"
    assert result.document_id is None
    assert result.web_url is None
    assert result.message is not None
    assert "ASN 77 already exists" in result.message


async def test_a_wait_returns_the_document_once_consumption_ends() -> None:
    setup = Setup(slow_reads=2)

    result = await setup.upload(wait_seconds=30)

    assert (result.state, result.wait) == ("success", "finished")
    assert result.document_id == setup.state.tasks[0].document_ids[0]
    assert result.waited_seconds == 4.0  # two polls of two seconds on the injected clock


async def test_a_wait_that_runs_out_reports_the_task_still_running() -> None:
    setup = Setup(slow_reads=10_000)

    result = await setup.upload(wait_seconds=5)

    assert (result.state, result.wait, result.document_id) == ("started", "timed_out", None)
    assert result.waited_seconds == 5.0
    assert result.task_id == setup.state.tasks[0].task_id


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

    result = await setup.service.from_path(
        str(folder / "scan März.pdf"), None, UploadMetadata(title="From disk")
    )

    assert (setup.sent.filename, setup.sent.content, setup.sent.title) == (
        "scan März.pdf",
        pdf("from disk"),
        "From disk",
    )
    assert result.filename == "scan März.pdf"


async def test_an_empty_name_given_with_a_path_is_refused_not_replaced(folder: Path) -> None:
    setup = Setup(upload_dirs=(str(folder),))

    with pytest.raises(Validation, match="file name"):
        _ = await setup.service.from_path(str(folder / "scan März.pdf"), "", UploadMetadata())

    assert setup.untouched()


async def test_a_name_given_with_the_path_wins(folder: Path) -> None:
    setup = Setup(upload_dirs=(str(folder),))

    _ = await setup.service.from_path(
        str(folder / "scan März.pdf"), "../renamed.pdf", UploadMetadata()
    )

    assert setup.sent.filename == "renamed.pdf"


async def test_a_path_outside_the_allowlist_sends_nothing(folder: Path) -> None:
    setup = Setup(upload_dirs=(str(folder),))

    with pytest.raises(NotFound):
        _ = await setup.service.from_path("/etc/passwd", None, UploadMetadata())
    with pytest.raises(NotFound):
        _ = await setup.service.from_path(f"{folder}/../../etc/passwd", None, UploadMetadata())

    assert setup.untouched()


async def test_without_an_allowlist_a_path_is_refused(folder: Path) -> None:
    setup = Setup()

    with pytest.raises(Validation, match="not enabled"):
        _ = await setup.service.from_path(str(folder / "scan März.pdf"), None, UploadMetadata())

    assert setup.untouched()


async def test_a_file_from_disk_gets_the_same_checks_as_content(folder: Path) -> None:
    setup = Setup(upload_dirs=(str(folder),))

    with pytest.raises(Validation, match="not a type Paperless can consume"):
        _ = await setup.service.from_path(str(folder / "notes.bin"), None, UploadMetadata())
    with pytest.raises(NotFound, match="No tag is named"):
        _ = await setup.service.from_path(
            str(folder / "scan März.pdf"), None, UploadMetadata(tags=("nope",))
        )

    assert setup.untouched()
