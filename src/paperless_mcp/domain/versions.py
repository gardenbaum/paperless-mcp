"""File versions of a document: what the port takes and what the version tools return.

Paperless 3.x keeps several files for one document. The root version is the original upload; later
versions are files uploaded to it (or documents merged into it). Title, tags and the other
metadata belong to the document, only the file and its extracted text belong to a version, and the
newest version is the one downloads and the text show.
"""

from typing import Annotated, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from paperless_mcp.domain.documents import DocumentVersion, UploadState
from paperless_mcp.domain.tasks import Wait


class VersionUpload(BaseModel):
    """A file to add as a new version of a document; the port sends it as is.

    Attributes:
        filename: The name the file is uploaded under.
        mime_type: What the content is; the services have sniffed it, Paperless does so again.
        label: The version label (at most 64 characters), or None for none.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    filename: str
    mime_type: str
    content: bytes = Field(repr=False)
    label: str | None = None


# Output models are tool output schemas: a one-line class docstring each, and field descriptions
# only where the name does not say enough.


class DocumentVersions(BaseModel):
    """The file versions of a document, newest first."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    document_id: int
    title: str
    current_version_id: Annotated[
        int, Field(description="The newest version: the file downloads and the text show.")
    ]
    versions: tuple[DocumentVersion, ...]
    web_url: Annotated[str, Field(description="Link to the document in the Paperless web UI.")]


class VersionUploadResult(BaseModel):
    """A file Paperless accepted as a new version and, if it was waited for, its outcome."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    task_id: Annotated[str, Field(description="Follow it with get_task; Paperless consumes later.")]
    state: Annotated[
        UploadState,
        Field(
            description=(
                "pending, started, success, failure or revoked; unknown when the task could not "
                "be read after the upload (e.g. this user may not view tasks): the upload was "
                "accepted all the same."
            )
        ),
    ]
    message: Annotated[
        str | None, Field(description="Why consumption failed, or why the state is unknown.")
    ]
    document_id: Annotated[int, Field(description="The document the version belongs to.")]
    version_id: Annotated[
        int | None, Field(description="The new version once consumption succeeded; else null.")
    ]
    web_url: Annotated[str, Field(description="Link to the document in the Paperless web UI.")]
    wait: Annotated[
        Wait,
        Field(
            description=(
                "not_requested: wait_seconds was 0; finished: consumption ended within the wait "
                "(see state); timed_out: still running, ask get_task later."
            )
        ),
    ]
    waited_seconds: float
    filename: Annotated[str, Field(description="The name the file was uploaded under.")]
    mime_type: Annotated[str, Field(description="The type found in the content.")]
    size_bytes: int


class VersionLabelSet(BaseModel):
    """A version after its label was set."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    document_id: int
    version: DocumentVersion
    web_url: Annotated[str, Field(description="Link to the document in the Paperless web UI.")]


class VersionDeleted(BaseModel):
    """A version that was deleted; the document and its other versions stay."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    document_id: int
    version_id: int
    current_version_id: Annotated[
        int, Field(description="The newest version that is left: downloads and text show it.")
    ]
    message: Annotated[str, Field(description="What happened and how to get the version back.")]


class VersionsMerged(BaseModel):
    """Documents that were merged into another document as its newest versions."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    document_id: Annotated[int, Field(description="The document that now holds the versions.")]
    merged_document_ids: Annotated[
        tuple[int, ...],
        Field(description="The documents that were merged in; they no longer exist on their own."),
    ]
    versions: Annotated[
        tuple[DocumentVersion, ...] | None,
        Field(description="Newest first; null if the document could not be read after the merge."),
    ]
    web_url: Annotated[str, Field(description="Link to the document in the Paperless web UI.")]
    message: Annotated[
        str | None, Field(description="Set when the versions could not be listed after the merge.")
    ] = None
