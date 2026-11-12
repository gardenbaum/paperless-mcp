"""Thumbnails and downloads of documents, with the size limit applied before the bytes are read.

Callers get a report for every file and the bytes only when the file is within the limit
(decision-7: files are delivered as MCP content, capped, and never written to the host).
"""

from pathlib import PurePath
from typing import TYPE_CHECKING

from paperless_mcp.domain.documents import FileDelivery, FileReport
from paperless_mcp.domain.errors import NotFound
from paperless_mcp.services.documents import document_link

if TYPE_CHECKING:
    from paperless_mcp.domain.documents import FileKind, FileMetadata, FileVersion
    from paperless_mcp.domain.ports import DocumentReader

PDF = "application/pdf"


def _over_limit(limit: int, link: str) -> str:
    return (
        f"The file is over the download limit of {limit} bytes, so it is not attached. "
        f"Open it at {link}, or ask the administrator to raise PAPERLESS_MCP_MAX_DOWNLOAD_BYTES."
    )


class FileService:
    """Reads the files of the documents the current user may see."""

    def __init__(self, reader: DocumentReader, *, web_url: str, max_bytes: int) -> None:
        """Read through ``reader``; files over ``max_bytes`` bytes are reported, not delivered.

        ``web_url`` is the base of links to the Paperless UI.
        """
        self._reader: DocumentReader = reader
        self._web_url: str = web_url
        self._max_bytes: int = max_bytes

    async def thumbnail(self, document_id: int) -> FileDelivery:
        """The thumbnail image of a document; thumbnails are small and have no limit.

        Raises:
            NotFound: No such document.
            PermissionDenied: The user may not see the document.
        """
        found = await self._reader.get_document_thumbnail(document_id)
        report = FileReport(
            document_id=document_id,
            kind="thumbnail",
            filename=found.filename,
            mime_type=found.mime_type,
            size_bytes=len(found.content),
            delivered=True,
            max_bytes=None,
            web_url=document_link(self._web_url, document_id),
            note=None,
        )
        return FileDelivery(report=report, data=found.content)

    async def download(self, document_id: int, version: FileVersion) -> FileDelivery:
        """The original or archive version of a document, if it is within the limit.

        The size comes from the file metadata, so a file over the limit is never downloaded;
        the size of the bytes that arrive is checked too.

        Raises:
            NotFound: No such document, or ``archive`` asked of a document without one.
            PermissionDenied: The user may not see the document.
        """
        metadata = await self._reader.get_document_metadata(document_id)
        archive = version == "archive"
        if archive and not metadata.has_archive_version:
            message = f"Document {document_id} has no archive version."
            raise NotFound(message, hint="Ask for the original: it is the only file it has.")
        size = metadata.original_size
        if archive and metadata.archive_size is not None:
            size = metadata.archive_size
        if size > self._max_bytes:
            return self._withheld(document_id, version, metadata, size)
        found = await self._reader.get_document_file(document_id, version)
        if len(found.content) > self._max_bytes:
            return self._withheld(document_id, version, metadata, len(found.content))
        report = self._report(
            document_id, version, found.filename, found.mime_type, len(found.content)
        )
        return FileDelivery(report=report, data=found.content)

    def _withheld(
        self, document_id: int, version: FileVersion, metadata: FileMetadata, size: int
    ) -> FileDelivery:
        """A report for a file over the limit, named and typed from the file metadata."""
        name = PurePath(metadata.original_filename)
        archive = version == "archive"
        report = self._report(
            document_id,
            version,
            name.with_suffix(".pdf").name if archive else name.name,
            PDF if archive else metadata.original_mime_type,
            size,
        )
        return FileDelivery(report=report, data=None)

    def _report(
        self, document_id: int, kind: FileKind, filename: str | None, mime_type: str, size: int
    ) -> FileReport:
        delivered = size <= self._max_bytes
        link = document_link(self._web_url, document_id)
        return FileReport(
            document_id=document_id,
            kind=kind,
            filename=filename,
            mime_type=mime_type,
            size_bytes=size,
            delivered=delivered,
            max_bytes=self._max_bytes,
            web_url=link,
            note=None if delivered else _over_limit(self._max_bytes, link),
        )
