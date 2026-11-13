"""Contract of DocumentWriter.upload_document: the same assertions against the fake and 3.4.2.

Every test uploads documents of its own (unique title and content) and registers what
consumption created with `backend.track_document`, so the live backend deletes it again.
Facts probed on 3.4.2: tags bring their parents and the inbox tag is added; the same content
twice makes two documents; an archive serial number that is taken fails the task, not the POST.
"""

import asyncio
import random
import time
import uuid
from datetime import date
from typing import TYPE_CHECKING

import pytest
from e2e.helpers import pdf

from paperless_mcp.domain.documents import CustomFieldValue, DocumentUpload
from paperless_mcp.domain.errors import PermissionDenied, Validation

if TYPE_CHECKING:
    from contract.conftest import Backend, Ports
    from paperless_mcp.domain.documents import DocumentRecord
    from paperless_mcp.domain.tasks import TaskStatus
    from paperless_mcp.domain.taxonomy import TaxonomyKind

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-6")]

CONTRACT = "DocumentWriter.upload_document"
CONSUMPTION_SECONDS = 180.0


async def ids(ports: Ports, kind: TaxonomyKind) -> dict[str, int]:
    return {item.name: item.id for item in await ports.taxonomy.list_taxonomy(kind)}


def unique(prefix: str = "contract-upload") -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def upload_of(text: str, **given: object) -> DocumentUpload:
    """A PDF whose text is `text`, also its title and file name: no other test has its content."""
    values: dict[str, object] = {
        "filename": f"{text}.pdf",
        "mime_type": "application/pdf",
        "content": pdf(text),
        "title": text,
    }
    return DocumentUpload.model_validate(values | given)


async def ended(backend: Backend, task_id: str) -> TaskStatus:
    """Wait for the task to end; the documents it created are tracked for clean-up."""
    deadline = time.monotonic() + CONSUMPTION_SECONDS
    while True:
        status = await backend.admin.tasks.get_task(task_id)
        if status.is_final:
            break
        assert time.monotonic() < deadline, f"Task {task_id} took too long"
        await asyncio.sleep(1)
    for document in status.document_ids:
        backend.track_document(document)
    return status


async def consumed(backend: Backend, upload: DocumentUpload) -> TaskStatus:
    """Upload through the port and wait until the task ends."""
    return await ended(backend, await backend.admin.document_writes.upload_document(upload))


async def read(ports: Ports, task: TaskStatus) -> DocumentRecord:
    [document] = task.document_ids
    return await ports.documents.get_document(document)


@pytest.mark.contract(CONTRACT)
async def test_an_upload_returns_the_id_of_a_task_that_creates_the_document(
    backend: Backend,
) -> None:
    upload = upload_of(unique())

    task_id = await backend.admin.document_writes.upload_document(upload)
    status = await ended(backend, task_id)

    assert str(uuid.UUID(task_id)) == task_id  # the Celery UUID, not a database id
    assert (status.task_id, status.task_type) == (task_id, "consume_file")
    assert (status.state, status.message) == ("success", None)
    assert (await read(backend.admin, status)).title == upload.title


@pytest.mark.contract(CONTRACT)
async def test_the_metadata_is_applied_and_tags_bring_their_parent_and_the_inbox(
    backend: Backend,
) -> None:
    ports = backend.admin
    tags, fields = await ids(ports, "tag"), await ids(ports, "custom_field")
    names = {
        "correspondent": (await ids(ports, "correspondent"))["e2e-acme"],
        "document_type": (await ids(ports, "document_type"))["e2e-invoice"],
        "storage_path": (await ids(ports, "storage_path"))["e2e-archive"],
    }
    serial = random.randrange(1_000_000, 4_000_000_000)  # noqa: S311 -- unique, not secret
    upload = upload_of(
        unique(),
        created=date(2025, 3, 4),
        archive_serial_number=serial,
        tags=(tags["e2e-invoices"],),
        custom_fields=(
            CustomFieldValue(field=fields["e2e-string"], value="from upload"),
            CustomFieldValue(field=fields["e2e-integer"], value=7),
            CustomFieldValue(field=fields["e2e-boolean"], value=True),
            CustomFieldValue(field=fields["e2e-monetary"], value="EUR12.50"),
            CustomFieldValue(field=fields["e2e-date"], value="2026-02-03"),
        ),
        **names,
    )

    record = await read(ports, await consumed(backend, upload))

    assert (record.created, record.archive_serial_number) == (date(2025, 3, 4), serial)
    assert (record.correspondent, record.document_type, record.storage_path) == (
        names["correspondent"],
        names["document_type"],
        names["storage_path"],
    )
    assert {tags["e2e-invoices"], tags["e2e-finance"], tags["e2e-inbox"]} <= set(record.tags)
    assert {v.field: v.value for v in record.custom_fields} == {
        fields["e2e-string"]: "from upload",
        fields["e2e-integer"]: 7,
        fields["e2e-boolean"]: True,
        fields["e2e-monetary"]: "EUR12.50",
        fields["e2e-date"]: "2026-02-03",
    }


@pytest.mark.contract(CONTRACT)
async def test_without_a_title_the_file_name_is_the_title_and_non_ascii_names_are_kept(
    backend: Backend,
) -> None:
    stem = unique("Käse-Rechnung")
    upload = upload_of(stem, title=None, filename=f"{stem}.pdf")

    task = await consumed(backend, upload)
    record = await read(backend.admin, task)
    metadata = await backend.admin.documents.get_document_metadata(record.id)

    assert record.title == stem
    assert record.original_file_name == f"{stem}.pdf"
    assert metadata.original_filename == f"{stem}.pdf"


@pytest.mark.contract(CONTRACT)
async def test_the_same_content_twice_makes_two_documents_on_3_4_2(backend: Backend) -> None:
    first = upload_of(unique())
    again = first.model_copy(update={"title": unique("contract-again")})

    one, two = await consumed(backend, first), await consumed(backend, again)

    assert (one.state, two.state) == ("success", "success")
    assert one.document_ids != two.document_ids


@pytest.mark.contract(CONTRACT)
async def test_a_taken_archive_serial_number_fails_the_task_not_the_upload(
    backend: Backend,
) -> None:
    serial = random.randrange(1_000_000, 4_000_000_000)  # noqa: S311 -- unique, not secret
    first = await consumed(backend, upload_of(unique(), archive_serial_number=serial))

    second = await consumed(backend, upload_of(unique(), archive_serial_number=serial))

    assert first.state == "success"
    assert second.state == "failure"
    assert second.document_ids == ()
    assert second.message is not None
    assert "ASN" in second.message


@pytest.mark.contract(CONTRACT)
async def test_a_file_type_paperless_cannot_consume_is_rejected_at_once(backend: Backend) -> None:
    upload = upload_of(
        unique(), filename="x.zip", mime_type="application/zip", content=b"PK\x03\x04"
    )

    with pytest.raises(Validation, match=r"document: File type .* not supported"):
        _ = await backend.admin.document_writes.upload_document(upload)


@pytest.mark.contract(CONTRACT)
@pytest.mark.parametrize("field", ["tags", "correspondent", "document_type", "storage_path"])
async def test_an_unknown_id_is_rejected_at_once_naming_the_field(
    backend: Backend, field: str
) -> None:
    unknown: object = (999_999,) if field == "tags" else 999_999

    with pytest.raises(Validation, match=f"{field}: Invalid pk"):
        _ = await backend.admin.document_writes.upload_document(
            upload_of(unique(), **{field: unknown})
        )


@pytest.mark.contract(CONTRACT)
async def test_a_custom_field_value_of_the_wrong_type_is_rejected_at_once(
    backend: Backend,
) -> None:
    field = (await ids(backend.admin, "custom_field"))["e2e-integer"]
    upload = upload_of(unique(), custom_fields=(CustomFieldValue(field=field, value="abc"),))

    with pytest.raises(Validation, match="custom_fields: Enter a valid integer"):
        _ = await backend.admin.document_writes.upload_document(upload)


@pytest.mark.contract(CONTRACT)
async def test_a_user_who_may_not_add_documents_is_denied(backend: Backend) -> None:
    with pytest.raises(PermissionDenied):
        _ = await backend.reader.document_writes.upload_document(upload_of(unique()))
