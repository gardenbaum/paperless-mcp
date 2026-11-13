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


# Why the tasks of a request are not all known: the user may not view tasks, or reading them failed.
type LookupProblem = Literal["forbidden", "failed"]


class StartedTasks(BaseModel):
    """The tasks Paperless started for a request, as far as they could be found.

    Attributes:
        ids: Their ids, oldest first, at most ``expected`` of them.
        expected: How many tasks the request starts.
        sources: The document each task belongs to, parallel to ``ids``; None where Paperless
            does not say (reprocessing). Empty when the caller does not know.
        problem: Why the lookup is incomplete beyond tasks that did not show up: the user may not
            view tasks (forbidden), or reading them failed after the request was accepted (failed).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    ids: tuple[str, ...]
    expected: int
    sources: tuple[int | None, ...] = ()
    problem: LookupProblem | None = None


# Output models are tool output schemas: a one-line class docstring each, and field descriptions
# only where the name does not say enough.


class PdfTask(BaseModel):
    """One Paperless task an operation started and how far it is."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    task_id: str
    state: Annotated[
        UploadState,
        Field(
            description=(
                "pending, started, success, failure, revoked, or unknown when the task could "
                "not be read."
            )
        ),
    ]
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
                "one still runs, else success. unknown when not all expected tasks could be "
                "found or read: Paperless accepted the request all the same (the user may not "
                "view tasks, or the work is not yet visible); see message."
            )
        ),
    ]
    message: Annotated[
        str | None, Field(description="Why a task failed, or why the state is unknown.")
    ]
    source_links: Annotated[
        tuple[str, ...], Field(description="Links to the source documents in the Paperless web UI.")
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
    produced_links: Annotated[
        tuple[str, ...],
        Field(
            description=(
                "Links to the produced documents; for a new version the link of the document "
                "that got it."
            )
        ),
    ]
    wait: Wait
    waited_seconds: float
