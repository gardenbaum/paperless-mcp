"""PDF operations: what the port takes and what the pdf tools return.

Paperless 3.x edits PDFs on the server. Every operation answers "OK" at once and runs as
background tasks that produce files: new documents, or new versions of the document. Where the
result goes is the ``Output`` of the operation.
"""

from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from paperless_mcp.domain.documents import UploadState
from paperless_mcp.domain.tasks import Wait

# Where the edited file goes: a new document (the original stays as it is), a new version of the
# same document (the older versions stay), or a new document while the original moves to the
# Paperless trash.
type Output = Literal["new_document", "new_version", "replace_original"]


class PageSlot(BaseModel):
    """One page of an edited file: which page of the source, turned how far, into which file.

    Attributes:
        page: The page of the source file, counted from 1.
        rotate: Degrees clockwise added to the page (a multiple of 90, negative turns back).
        document: The number of the output file the page goes to, from 0; pages keep the order
            they are listed in within their file.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    page: Annotated[int, Field(description="Page of the source file, counted from 1.")]
    rotate: Annotated[
        int,
        Field(
            description="Degrees to turn the page clockwise: 0, 90, 180 or 270 (-90 turns back)."
        ),
    ] = 0
    document: Annotated[
        int,
        Field(
            description=(
                "Number of the output file this page goes to, from 0 without gaps; pages keep "
                "the order they are listed in within their file."
            )
        ),
    ] = 0


# Output models are tool output schemas: a one-line class docstring each, and field descriptions
# only where the name does not say enough.


class PdfTask(BaseModel):
    """One Paperless task an operation started and how far it is."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    task_id: str
    state: Annotated[UploadState, Field(description="pending, started, success, failure, revoked.")]
    message: Annotated[str | None, Field(description="Why the task failed; null otherwise.")]
    document_ids: Annotated[
        tuple[int, ...],
        Field(description="The document or version the task produced, once it succeeded."),
    ]


class PdfResult(BaseModel):
    """A PDF operation Paperless accepted, the tasks it started and, if waited for, the outcome."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    operation: Annotated[str, Field(description="What was asked, e.g. rotate or merge.")]
    summary: Annotated[str, Field(description="What Paperless was asked to do, in words.")]
    source_document_ids: Annotated[
        tuple[int, ...], Field(description="The documents the operation worked on.")
    ]
    state: Annotated[
        UploadState,
        Field(
            description=(
                "The tasks together: failure or revoked if one did, else started or pending if "
                "one still runs, else success. unknown when no task could be found or read: "
                "Paperless accepted the request all the same, and the user may not view tasks "
                "or the work is not yet visible."
            )
        ),
    ]
    message: Annotated[
        str | None, Field(description="Why a task failed, or why the state is unknown.")
    ]
    tasks: tuple[PdfTask, ...]
    produced_ids: Annotated[
        tuple[int, ...],
        Field(
            description=(
                "The documents the finished tasks produced: new documents, or for a new version "
                "the id of that version (the document keeps its id). Empty while tasks run; "
                "reprocessing produces none."
            )
        ),
    ]
    wait: Wait
    waited_seconds: float
