"""Thumbnails and downloads of documents, with the size limit applied before the bytes are read.

Callers get a report for every file and the bytes only when the file is within the limit
(decision-7: files are delivered as MCP content, capped, and never written to the host).
"""

from pathlib import PurePath
from typing import TYPE_CHECKING

from paperless_mcp.domain.documents import FileDelivery, FileReport, Thumbnail
from paperless_mcp.domain.errors import NotFound, PaperlessMcpError
from paperless_mcp.services.documents import document_link

if TYPE_CHECKING:
    from paperless_mcp.domain.documents import FileKind, FileVersion
    from paperless_mcp.domain.ports import DocumentReader

PDF = "application/pdf"


def _over_limit(limit: int, link: str) -> str:
    return (
        f"The file is over the download limit of {limit} bytes, so it is not attached. "
        f"Read its text with get_document_content, open it at {link}, or ask the "
        "administrator to raise PAPERLESS_MCP_MAX_DOWNLOAD_BYTES."
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

    async def thumbnail(self, document_id: int) -> Thumbnail:
        """The thumbnail image of a document; thumbnails are small and have no limit.

        Raises:
            NotFound: No such document.
            PermissionDenied: The user may not see the document.
            PaperlessMcpError: Paperless sent an empty image.
        """
        found = await self._reader.get_document_thumbnail(document_id)
        if not found.content:
            message = f"Paperless sent an empty thumbnail for document {document_id}."
            raise PaperlessMcpError(message)
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
        return Thumbnail(report=report, data=found.content)

    async def download(self, document_id: int, version: FileVersion) -> FileDelivery:
        """The original or archive version of a document, if it is within the limit.

        A size the file metadata gives decides before anything is downloaded; otherwise (an
        archive version of unknown size, or metadata that understates it) the download itself
        stops at the limit, so no more than the limit is ever read.

        Raises:
            NotFound: No such document, or ``archive`` asked of a document without one.
            PermissionDenied: The user may not see the document.
        """
        metadata = await self._reader.get_document_metadata(document_id)
        archive = version == "archive"
        if archive and not metadata.has_archive_version:
            message = f"Document {document_id} has no archive version."
            hint = (
                'Call download_document with kind="original"; it is the only file the document has.'
            )
            raise NotFound(message, hint=hint)
        size: int | None = metadata.archive_size if archive else metadata.original_size
        name = PurePath(metadata.original_filename).name
        if size is not None and size > self._max_bytes:
            upload = (
                (PurePath(name).with_suffix(".pdf").name if archive else name) if name else None
            )
            mime_type = PDF if archive else metadata.original_mime_type
            return self._delivery(
                document_id, version, filename=upload, mime_type=mime_type, size=size, data=None
            )
        found = await self._reader.get_document_file(
            document_id, version, max_bytes=self._max_bytes
        )
        if found.truncated:
            return self._delivery(
                document_id,
                version,
                filename=found.filename,
                mime_type=found.mime_type,
                size=None,
                data=None,
            )
        return self._delivery(
            document_id,
            version,
            filename=found.filename,
            mime_type=found.mime_type,
            size=len(found.content),
            data=found.content,
        )

    def _delivery(  # noqa: PLR0913 -- the parts of one report
        self,
        document_id: int,
        kind: FileKind,
        *,
        filename: str | None,
        mime_type: str,
        size: int | None,
        data: bytes | None,
    ) -> FileDelivery:
        link = document_link(self._web_url, document_id)
        report = FileReport(
            document_id=document_id,
            kind=kind,
            filename=filename,
            mime_type=mime_type,
            size_bytes=size,
            delivered=data is not None,
            max_bytes=self._max_bytes,
            web_url=link,
            note=None if data is not None else _over_limit(self._max_bytes, link),
        )
        return FileDelivery(report=report, data=data)
