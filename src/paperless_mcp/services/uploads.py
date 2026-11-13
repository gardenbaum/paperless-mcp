"""Uploading a document: a file and its metadata by name, checked, then handed to Paperless.

Everything that can be checked here is checked before the first byte is sent, because an upload
cannot be taken back: the file name, the file's type (from its bytes), its size, every name
(correspondent, document type, storage path, tags, custom fields) resolved to an id, every
custom field value valid for its data type, linked documents visible. Tags that do not exist are
not created (unlike ``update_document``): an upload with a misspelt tag is more likely a mistake
than a wish.

Paperless answers an upload with the id of a consumption task, and consumes later. A caller who
wants the new document waits a bounded time (``TaskService``). Problems only consumption finds,
such as an archive serial number that is taken, fail the task and come back as ``failure``.
Uploading the same content twice makes two documents on Paperless 3.4.2; nothing here prevents
or detects that.
"""

from typing import TYPE_CHECKING

from paperless_mcp.domain.documents import CustomFieldValue, DocumentUpload, UploadResult
from paperless_mcp.domain.errors import PaperlessMcpError, Validation
from paperless_mcp.services.custom_values import linked, require_visible, resolve_values
from paperless_mcp.services.document_edits import MAX_TITLE
from paperless_mcp.services.documents import document_link
from paperless_mcp.services.upload_source import (
    clean_filename,
    decode_base64,
    read_allowed,
)
from paperless_mcp.services.upload_types import sniff

if TYPE_CHECKING:
    from collections.abc import Sequence

    from paperless_mcp.domain.documents import NameOrId, UploadMetadata
    from paperless_mcp.domain.ports import DocumentReader, DocumentWriter
    from paperless_mcp.domain.taxonomy import TaxonomyKind
    from paperless_mcp.services.tasks import TaskService
    from paperless_mcp.services.taxonomy import TaxonomyResolver

# Paperless stores the archive serial number in an unsigned 32-bit column.
MAX_ASN = 4_294_967_295
UNSUPPORTED = (
    "PDF, PNG, JPEG, GIF, TIFF, BMP, WebP, plain text and CSV; Word, Excel, PowerPoint, "
    "OpenDocument and RTF files only if Paperless runs Tika and Gotenberg."
)


NO_DOCUMENT = (
    "Paperless consumed the file but names no document for it (it may have been split into "
    "several, or the new document is hidden from this user)."
)


class UploadService:
    """Uploads files to Paperless."""

    def __init__(  # noqa: PLR0913 -- the ports and settings the use case needs
        self,
        reader: DocumentReader,
        writer: DocumentWriter,
        taxonomy: TaxonomyResolver,
        tasks: TaskService,
        *,
        web_url: str,
        max_bytes: int,
        upload_dirs: Sequence[str] = (),
    ) -> None:
        """Upload through ``writer``, resolve names with ``taxonomy``, wait with ``tasks``.

        ``max_bytes`` bounds the file; ``upload_dirs`` is the allowlist for ``from_path``.
        """
        self._reader: DocumentReader = reader
        self._writer: DocumentWriter = writer
        self._taxonomy: TaxonomyResolver = taxonomy
        self._tasks: TaskService = tasks
        self._web_url: str = web_url
        self._max_bytes: int = max_bytes
        self._dirs: tuple[str, ...] = tuple(upload_dirs)

    async def from_content(
        self,
        content_base64: str,
        filename: str,
        metadata: UploadMetadata,
        *,
        wait_seconds: float = 0,
    ) -> UploadResult:
        """Upload base64 ``content_base64`` under ``filename``.

        Raises:
            Validation: The content, its type, the name or a value is not acceptable.
            NotFound: A name or linked document matches nothing.
            Ambiguous: A name matches more than one object.
            PermissionDenied: The user may not add documents.
        """
        name = clean_filename(filename)
        data = decode_base64(content_base64, max_bytes=self._max_bytes)
        return await self._upload(data, name, metadata, wait_seconds)

    async def from_path(
        self,
        path: str,
        filename: str | None,
        metadata: UploadMetadata,
        *,
        wait_seconds: float = 0,
    ) -> UploadResult:
        """Upload the file at ``path``, which must lie in an allowlisted directory.

        The name is ``filename``, else the file's own. Raises like ``from_content``, and
        ``NotFound`` when the path is outside the allowlist or not a regular file.
        """
        found, data = read_allowed(path, self._dirs, max_bytes=self._max_bytes)
        name = clean_filename(found if filename is None else filename)
        return await self._upload(data, name, metadata, wait_seconds)

    async def _upload(
        self, data: bytes, name: str, metadata: UploadMetadata, wait_seconds: float
    ) -> UploadResult:
        mime_type = sniff(data, name)
        if mime_type is None:
            message = "The file is not a type Paperless can consume."
            raise Validation(message, hint=f"Supported: {UNSUPPORTED}")
        upload = await self._prepared(data, name, mime_type, metadata)
        task_id = await self._writer.upload_document(upload)
        return await self._result(task_id, upload, wait_seconds)

    async def _prepared(
        self, data: bytes, name: str, mime_type: str, metadata: UploadMetadata
    ) -> DocumentUpload:
        """The upload with every name an id and every value checked; nothing is sent."""
        title = None if metadata.title is None else metadata.title.strip()
        if title is not None and not title:
            message = "The title cannot be blank."
            raise Validation(message, hint="Pass a title, or leave it out to use the file name.")
        if title is not None and len(title) > MAX_TITLE:
            message = f"The title is longer than {MAX_TITLE} characters."
            raise Validation(message, hint="Shorten the title.")
        number = metadata.archive_serial_number
        if number is not None and not 0 <= number <= MAX_ASN:
            message = f"The archive serial number must be between 0 and {MAX_ASN}."
            raise Validation(message, hint="Pass an unused whole number in that range.")
        references: dict[TaxonomyKind, NameOrId | None] = {
            "correspondent": metadata.correspondent,
            "document_type": metadata.document_type,
            "storage_path": metadata.storage_path,
        }
        ids = {
            kind: (await self._taxonomy.resolve(kind, value)).id
            for kind, value in references.items()
            if value is not None
        }
        tags = [(await self._taxonomy.resolve("tag", tag)).id for tag in metadata.tags]
        resolved = await resolve_values(self._taxonomy, metadata.custom_fields)
        await require_visible(
            self._reader,
            {
                n
                for item, value in resolved.values()
                if item.data_type == "documentlink"
                for n in linked(value)
            },
        )
        return DocumentUpload(
            filename=name,
            mime_type=mime_type,
            content=data,
            title=title,
            created=metadata.created,
            archive_serial_number=number,
            correspondent=ids.get("correspondent"),
            document_type=ids.get("document_type"),
            storage_path=ids.get("storage_path"),
            tags=tuple(dict.fromkeys(tags)),
            custom_fields=tuple(
                CustomFieldValue(field=field, value=value) for field, (_, value) in resolved.items()
            ),
        )

    async def _result(
        self, task_id: str, upload: DocumentUpload, wait_seconds: float
    ) -> UploadResult:
        """What the upload did, after the wait; the task id alone if the task cannot be read."""
        try:
            report = await self._tasks.get(task_id, wait_seconds=wait_seconds)
            document = (
                report.document_ids[0]
                if report.state == "success" and report.document_ids
                else None
            )
        except Exception as error:  # noqa: BLE001 -- the file is in Paperless: the task id must get out
            # An error here would invite a retry, and with it a second document.
            reason = error.message if isinstance(error, PaperlessMcpError) else "unexpected error"
            return UploadResult(
                task_id=task_id,
                state="unknown",
                message=(
                    f"Paperless accepted the file, but its task could not be read ({reason}); "
                    "the outcome is unknown. Follow the task with get_task."
                ),
                document_id=None,
                web_url=None,
                wait="timed_out" if wait_seconds > 0 else "not_requested",
                waited_seconds=0,
                filename=upload.filename,
                mime_type=upload.mime_type,
                size_bytes=len(upload.content),
            )
        note = NO_DOCUMENT if report.state == "success" and document is None else None
        return UploadResult(
            task_id=task_id,
            state=report.state,
            message=report.message or note,
            document_id=document,
            web_url=None if document is None else document_link(self._web_url, document),
            wait=report.wait,
            waited_seconds=report.waited_seconds,
            filename=upload.filename,
            mime_type=upload.mime_type,
            size_bytes=len(upload.content),
        )
