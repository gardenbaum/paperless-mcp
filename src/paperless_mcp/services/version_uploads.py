"""Uploading a file as a new version of a document, with the checks of ``upload_document``.

Everything that can be checked is checked before the first byte is sent, because an upload cannot
be taken back: the file name, the file's type (from its bytes), its size, the label and the
document. The file, the size limit and the path allowlist are the same as for new documents
(``UploadService``). The new version becomes the newest one, which is the file and text the
document shows; the old versions stay. Paperless answers with the id of a consumption task and
consumes later. Uploading the same content twice makes two versions; nothing here prevents or
detects that.
"""

from typing import TYPE_CHECKING

from paperless_mcp.domain.errors import Validation
from paperless_mcp.domain.versions import VersionUpload, VersionUploadResult
from paperless_mcp.services.documents import document_link
from paperless_mcp.services.task_outcome import follow
from paperless_mcp.services.upload_source import clean_filename, decode_base64, read_allowed
from paperless_mcp.services.upload_types import sniff
from paperless_mcp.services.uploads import UNSUPPORTED
from paperless_mcp.services.versions import clean_label, require_change

if TYPE_CHECKING:
    from collections.abc import Sequence

    from paperless_mcp.domain.ports import DocumentReader, DocumentWriter
    from paperless_mcp.services.tasks import TaskService

NO_VERSION = (
    "Paperless consumed the file but names no version for it (the new version may be hidden "
    "from this user)."
)


class VersionUploadService:
    """Uploads files as new versions of documents."""

    def __init__(  # noqa: PLR0913 -- the ports and settings the use case needs
        self,
        reader: DocumentReader,
        writer: DocumentWriter,
        tasks: TaskService,
        *,
        web_url: str,
        max_bytes: int,
        upload_dirs: Sequence[str] = (),
    ) -> None:
        """Upload through ``writer``, check the document with ``reader``, wait with ``tasks``.

        ``max_bytes`` bounds the file; ``upload_dirs`` is the allowlist for ``from_path``.
        """
        self._reader: DocumentReader = reader
        self._writer: DocumentWriter = writer
        self._tasks: TaskService = tasks
        self._web_url: str = web_url
        self._max_bytes: int = max_bytes
        self._dirs: tuple[str, ...] = tuple(upload_dirs)

    async def from_content(
        self,
        document_id: int,
        content_base64: str,
        filename: str,
        label: str | None,
        *,
        wait_seconds: float = 0,
    ) -> VersionUploadResult:
        """Upload base64 ``content_base64`` under ``filename`` as a new version.

        Raises:
            Validation: The content, its type, the name or the label is not acceptable.
            NotFound: No such document, or the user may not see it.
            PermissionDenied: The user may not change the document.
        """
        name = clean_filename(filename)
        data = decode_base64(content_base64, max_bytes=self._max_bytes)
        return await self._upload(document_id, data, name, label, wait_seconds)

    async def from_path(
        self,
        document_id: int,
        path: str,
        filename: str | None,
        label: str | None,
        *,
        wait_seconds: float = 0,
    ) -> VersionUploadResult:
        """Upload the file at ``path``, which must lie in an allowlisted directory.

        The name is ``filename``, else the file's own. Raises like ``from_content``, and
        ``NotFound`` when the path is outside the allowlist or not a regular file.
        """
        found, data = read_allowed(path, self._dirs, max_bytes=self._max_bytes)
        name = clean_filename(found if filename is None else filename)
        return await self._upload(document_id, data, name, label, wait_seconds)

    async def _upload(
        self, document_id: int, data: bytes, name: str, label: str | None, wait_seconds: float
    ) -> VersionUploadResult:
        mime_type = sniff(data, name)
        if mime_type is None:
            message = "The file is not a type Paperless can consume."
            raise Validation(message, hint=f"Supported: {UNSUPPORTED}")
        text = clean_label(label)
        require_change(await self._reader.get_document(document_id))  # missing, hidden or not ours
        upload = VersionUpload(filename=name, mime_type=mime_type, content=data, label=text)
        task_id = await self._writer.upload_document_version(document_id, upload)
        outcome = await follow(self._tasks, task_id, wait_seconds, nothing_named=NO_VERSION)
        return VersionUploadResult(
            task_id=task_id,
            state=outcome.state,
            message=outcome.message,
            document_id=document_id,
            version_id=outcome.document_id,
            web_url=document_link(self._web_url, document_id),
            wait=outcome.wait,
            waited_seconds=outcome.waited_seconds,
            filename=name,
            mime_type=mime_type,
            size_bytes=len(data),
        )
