"""Contract of PdfWriter: the same assertions on the fake and on Paperless 3.4.2.

Probed on 3.4.2: every PDF endpoint answers "OK" and names no task (the adapter finds the tasks);
rotating, and editing or unlocking as a new version, add a version to the same document and keep
the older one; the other forms make new documents; deleting originals sends them to the trash
after the new document exists; a user who may not view tasks gets the work done and no task id.
Every test works on documents of its own, never on a seeded one.
"""

import asyncio
import time
from typing import TYPE_CHECKING

import pytest

from paperless_mcp.domain.documents import DocumentPatch
from paperless_mcp.domain.errors import PermissionDenied, Validation
from paperless_mcp.domain.pdf import PageSlot

if TYPE_CHECKING:
    from collections.abc import Callable

    from contract.conftest import Backend
    from paperless_mcp.domain.pdf import StartedTasks

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-6")]

ROTATE = "PdfWriter.rotate_documents"
MERGE = "PdfWriter.merge_documents"
EDIT = "PdfWriter.edit_pdf"
PASSWORD = "PdfWriter.remove_password"
REPROCESS = "PdfWriter.reprocess_documents"
MISSING = 999_999
SECONDS = 180.0
# The global permissions of a user who may change documents and make new ones (not delete).
EDITING = frozenset({"view_document", "change_document", "add_document"})


async def finished(backend: Backend, started: StartedTasks) -> list[int]:
    """Wait for the tasks (read as the admin), track what they made, return those ids."""
    produced: list[int] = []
    for task_id in started.ids:
        deadline = time.monotonic() + SECONDS
        while True:
            status = await backend.admin.tasks.get_task(task_id)
            if status.is_final:
                break
            assert time.monotonic() < deadline, f"Task {task_id} took too long"
            await asyncio.sleep(0.5)
        assert status.state == "success", status.message
        produced += status.document_ids
    for number in produced:
        backend.track_document(number)
    return produced


async def until(condition: Callable[[], bool]) -> None:
    """Wait until `condition` holds: Paperless deletes originals after the new file exists."""
    deadline = time.monotonic() + SECONDS
    while not condition():
        assert time.monotonic() < deadline, "Paperless did not finish in time"
        await asyncio.sleep(0.5)


async def pages(backend: Backend, document: int) -> int | None:
    return (await backend.admin.documents.get_document(document)).page_count


async def version_ids(backend: Backend, document: int) -> list[int]:
    return [v.id for v in (await backend.admin.documents.get_document(document)).versions]


@pytest.mark.contract(ROTATE)
async def test_rotating_adds_a_version_to_each_document_and_names_a_task_for_each(
    backend: Backend,
) -> None:
    first, second = backend.new_pdf(2), backend.new_pdf(1)

    tasks = await backend.admin.pdf_writes.rotate_documents((first, second), 90)

    assert (len(tasks.ids), tasks.expected, tasks.problem) == (2, 2, None)
    produced = await finished(backend, tasks)
    for document, version in zip((first, second), produced, strict=True):
        assert await version_ids(backend, document) == [version, document]
    assert await pages(backend, first) == 2  # rotating keeps the pages


@pytest.mark.contract(ROTATE)
@pytest.mark.contract(MERGE)
@pytest.mark.contract(EDIT)
@pytest.mark.contract(PASSWORD)
@pytest.mark.contract(REPROCESS)
async def test_a_request_for_unknown_or_repeated_documents_is_refused(backend: Backend) -> None:
    writes, document = backend.admin.pdf_writes, backend.new_documents(1)[0]
    slot = (PageSlot(page=1),)

    for unknown in ((MISSING,), (document, document)):
        with pytest.raises(Validation, match="exist"):
            _ = await writes.rotate_documents(unknown, 90)
        with pytest.raises(Validation, match="exist"):
            _ = await writes.merge_documents(
                unknown, metadata_document_id=None, delete_originals=False
            )
        with pytest.raises(Validation, match="exist"):
            _ = await writes.reprocess_documents(unknown, remote_ocr=False)
    with pytest.raises(Validation, match="exist"):
        _ = await writes.edit_pdf(MISSING, slot, output="new_document")
    with pytest.raises(Validation, match="exist"):
        _ = await writes.remove_password(MISSING, "x", output="new_document")


@pytest.mark.contract(ROTATE)
async def test_degrees_must_be_a_multiple_of_90(backend: Backend) -> None:
    document = backend.new_documents(1)[0]

    with pytest.raises(Validation, match="multiple of 90"):
        _ = await backend.admin.pdf_writes.rotate_documents((document,), 45)


@pytest.mark.contract(MERGE)
async def test_merging_makes_one_new_document_titled_after_the_metadata_document(
    backend: Backend,
) -> None:
    first, second = backend.new_pdf(2), backend.new_pdf(1)
    source = await backend.admin.documents.get_document(second)

    tasks = await backend.admin.pdf_writes.merge_documents(
        (first, second), metadata_document_id=second, delete_originals=False
    )

    [merged] = await finished(backend, tasks)
    made = await backend.admin.documents.get_document(merged)
    assert (made.title, made.page_count) == (f"{source.title} (merged)", 3)
    assert not backend.in_trash(first)
    assert (await pages(backend, first), await pages(backend, second)) == (2, 1)


@pytest.mark.contract(MERGE)
async def test_merging_can_send_the_originals_to_the_trash(backend: Backend) -> None:
    first, second = backend.new_pdf(1), backend.new_pdf(1)

    tasks = await backend.admin.pdf_writes.merge_documents(
        (first, second), metadata_document_id=None, delete_originals=True
    )

    [merged] = await finished(backend, tasks)
    await until(lambda: backend.in_trash(first) and backend.in_trash(second))
    assert await pages(backend, merged) == 2


@pytest.mark.contract(EDIT)
async def test_an_edit_splits_pages_into_new_documents_and_leaves_the_original(
    backend: Backend,
) -> None:
    document = backend.new_pdf(3)
    slots = (
        PageSlot(page=3),
        PageSlot(page=1, rotate=90),
        PageSlot(page=2, document=1),
    )

    tasks = await backend.admin.pdf_writes.edit_pdf(document, slots, output="new_document")

    assert len(tasks.ids) == 2
    produced = await finished(backend, tasks)
    assert sorted([await pages(backend, n) for n in produced], key=lambda n: n or 0) == [1, 2]
    assert await pages(backend, document) == 3
    assert not backend.in_trash(document)


@pytest.mark.contract(EDIT)
async def test_an_edit_as_a_new_version_changes_what_the_document_shows_and_keeps_the_old(
    backend: Backend,
) -> None:
    document = backend.new_pdf(3)

    tasks = await backend.admin.pdf_writes.edit_pdf(
        document, (PageSlot(page=1), PageSlot(page=3)), output="new_version"
    )

    [version] = await finished(backend, tasks)
    assert await version_ids(backend, document) == [version, document]
    assert await pages(backend, document) == 2


@pytest.mark.contract(EDIT)
async def test_an_edit_can_replace_the_original_by_trashing_it(backend: Backend) -> None:
    document = backend.new_pdf(2)

    tasks = await backend.admin.pdf_writes.edit_pdf(
        document, (PageSlot(page=2),), output="replace_original"
    )

    [made] = await finished(backend, tasks)
    await until(lambda: backend.in_trash(document))
    assert await pages(backend, made) == 1


@pytest.mark.contract(EDIT)
async def test_a_bad_edit_is_refused_before_anything_happens(backend: Backend) -> None:
    writes, document = backend.admin.pdf_writes, backend.new_pdf(2)
    text = backend.upload(f"contract-text-{document}", "plain.txt", b"plain text", "text/plain")

    with pytest.raises(Validation, match="out of bounds"):
        _ = await writes.edit_pdf(document, (PageSlot(page=3),), output="new_document")
    with pytest.raises(Validation, match="out of bounds"):
        _ = await writes.edit_pdf(
            document, (PageSlot(page=1), PageSlot(page=1, document=2)), output="new_document"
        )
    with pytest.raises(Validation, match="single output"):
        _ = await writes.edit_pdf(
            document, (PageSlot(page=1), PageSlot(page=2, document=1)), output="new_version"
        )
    with pytest.raises(Validation, match="multiple of 90"):
        _ = await writes.edit_pdf(document, (PageSlot(page=1, rotate=45),), output="new_document")
    with pytest.raises(Validation, match="empty"):
        _ = await writes.edit_pdf(document, (), output="new_document")
    with pytest.raises(Validation, match="PDF"):
        _ = await writes.edit_pdf(text, (PageSlot(page=1),), output="new_document")

    assert await version_ids(backend, document) == [document]


@pytest.mark.contract(PASSWORD)
async def test_a_password_is_removed_into_a_new_document(backend: Backend) -> None:
    locked = backend.new_encrypted("s3cret")

    tasks = await backend.admin.pdf_writes.remove_password(locked, "s3cret", output="new_document")

    [unlocked] = await finished(backend, tasks)
    assert unlocked != locked
    assert await pages(backend, unlocked) == 1
    assert await version_ids(backend, locked) == [locked]


@pytest.mark.contract(PASSWORD)
async def test_a_password_can_be_removed_as_a_new_version_and_a_wrong_one_is_refused(
    backend: Backend,
) -> None:
    locked = backend.new_encrypted("s3cret")
    writes = backend.admin.pdf_writes

    with pytest.raises(Validation, match="password"):
        _ = await writes.remove_password(locked, "wrong", output="new_version")
    tasks = await writes.remove_password(locked, "s3cret", output="new_version")

    [version] = await finished(backend, tasks)
    assert await version_ids(backend, locked) == [version, locked]
    assert await pages(backend, locked) == 1


@pytest.mark.contract(PASSWORD)
async def test_a_password_can_be_removed_while_trashing_the_locked_original(
    backend: Backend,
) -> None:
    locked = backend.new_encrypted("s3cret")

    tasks = await backend.admin.pdf_writes.remove_password(
        locked, "s3cret", output="replace_original"
    )

    _ = await finished(backend, tasks)
    await until(lambda: backend.in_trash(locked))


@pytest.mark.contract(PASSWORD)
async def test_a_file_without_a_password_starts_no_task_and_a_text_file_is_refused(
    backend: Backend,
) -> None:
    writes = backend.admin.pdf_writes
    document = backend.new_pdf(1)
    text = backend.upload(f"contract-text-{document}", "plain.txt", b"plain text", "text/plain")

    tasks = await writes.remove_password(document, "x", output="new_document")
    with pytest.raises(Validation, match="PDF"):
        _ = await writes.remove_password(text, "x", output="new_document")

    assert tasks.ids == ()


@pytest.mark.contract(REPROCESS)
async def test_reprocessing_starts_one_task_per_document(backend: Backend) -> None:
    first, second = backend.new_documents(2)

    tasks = await backend.admin.pdf_writes.reprocess_documents((first, second), remote_ocr=False)

    assert len(tasks.ids) == 2
    assert await finished(backend, tasks) == []  # reprocessing names no document


@pytest.mark.contract(ROTATE)
@pytest.mark.contract(MERGE)
@pytest.mark.contract(EDIT)
@pytest.mark.contract(PASSWORD)
@pytest.mark.contract(REPROCESS)
async def test_only_a_user_who_may_change_the_documents_can_edit_them(backend: Backend) -> None:
    first, second = backend.new_documents(2)
    viewer = backend.restricted(EDITING, view=[first, second])

    for user in (viewer, backend.blind):
        writes = user.pdf_writes
        with pytest.raises(PermissionDenied):
            _ = await writes.rotate_documents((first,), 90)
        with pytest.raises(PermissionDenied):
            _ = await writes.edit_pdf(first, (PageSlot(page=1),), output="new_document")
        with pytest.raises(PermissionDenied):
            _ = await writes.remove_password(first, "x", output="new_document")
        with pytest.raises(PermissionDenied):
            _ = await writes.reprocess_documents((first,), remote_ocr=False)
        with pytest.raises(PermissionDenied):
            _ = await writes.merge_documents(
                (first, second), metadata_document_id=None, delete_originals=False
            )


@pytest.mark.contract(ROTATE)
@pytest.mark.contract(EDIT)
@pytest.mark.contract(PASSWORD)
@pytest.mark.contract(REPROCESS)
async def test_a_change_grant_reprocesses_but_rotating_and_editing_need_ownership(
    backend: Backend,
) -> None:
    document = backend.new_documents(1)[0]
    granted = backend.restricted(EDITING, change=[document])
    writes = granted.pdf_writes

    with pytest.raises(PermissionDenied):
        _ = await writes.rotate_documents((document,), 90)
    with pytest.raises(PermissionDenied):
        _ = await writes.edit_pdf(document, (PageSlot(page=1),), output="new_document")
    with pytest.raises(PermissionDenied):
        _ = await writes.remove_password(document, "x", output="new_document")
    assert (await writes.reprocess_documents((document,), remote_ocr=False)).ids == ()


@pytest.mark.contract(EDIT)
async def test_making_new_documents_needs_the_right_to_add_them(backend: Backend) -> None:
    document = backend.new_pdf(1)
    _ = await backend.admin.document_writes.update_document(document, DocumentPatch(owner=None))
    changer = backend.restricted(frozenset({"view_document", "change_document"}))

    with pytest.raises(PermissionDenied):
        _ = await changer.pdf_writes.edit_pdf(document, (PageSlot(page=1),), output="new_document")
    tasks = await changer.pdf_writes.edit_pdf(document, (PageSlot(page=1),), output="new_version")

    assert tasks.ids == ()  # this user may not view tasks
    await until_versions(backend, document, 2)


@pytest.mark.contract(ROTATE)
async def test_a_user_who_may_not_view_tasks_gets_the_work_done_and_no_task_id(
    backend: Backend,
) -> None:
    document = backend.new_pdf(1)
    _ = await backend.admin.document_writes.update_document(document, DocumentPatch(owner=None))
    editor = backend.restricted(EDITING)

    tasks = await editor.pdf_writes.rotate_documents((document,), 90)

    assert (tasks.ids, tasks.expected, tasks.problem) == ((), 1, "forbidden")
    await until_versions(backend, document, 2)


async def until_versions(backend: Backend, document: int, count: int) -> None:
    deadline = time.monotonic() + SECONDS
    while len(await version_ids(backend, document)) < count:
        assert time.monotonic() < deadline, "Paperless did not finish in time"
        await asyncio.sleep(0.5)
    for number in await version_ids(backend, document):
        backend.track_document(number)


@pytest.mark.contract(MERGE)
async def test_deleting_originals_needs_the_right_to_delete_them(backend: Backend) -> None:
    first, second = backend.new_documents(2)
    editor = backend.restricted(EDITING, change=[first, second])

    with pytest.raises(PermissionDenied):
        _ = await editor.pdf_writes.merge_documents(
            (first, second), metadata_document_id=None, delete_originals=True
        )

    assert not backend.in_trash(first)
