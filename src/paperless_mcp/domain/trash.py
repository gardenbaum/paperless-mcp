"""The Paperless trash: what the port returns and what the trash tools answer.

Deleting a document moves it to the trash, where it stays until the trash is emptied (Paperless
does so by itself after 30 days by default). Deleting a version of a document puts that version's
file there too, as an entry of its own. Restoring puts the document back, or re-attaches the
version to its document; emptying removes the entries for good.
"""

from datetime import datetime
from typing import Annotated, ClassVar

from pydantic import BaseModel, ConfigDict, Field


class TrashedDocument(BaseModel):
    """An entry of the trash: a deleted document, or a version deleted from a document."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: Annotated[
        int, Field(description="Pass it to restore_from_trash or empty_trash (a version's id).")
    ]
    title: str
    deleted_at: datetime
    version_of: Annotated[
        int | None,
        Field(
            description=(
                "Set when the entry is a version (a file) deleted from the document with this id; "
                "restoring it re-attaches it to that document. Null for a deleted document."
            )
        ),
    ]


class TrashPage(BaseModel):
    """One page of the trash as the port reads it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    count: int
    next_page: int | None
    entries: tuple[TrashedDocument, ...]


class TrashList(BaseModel):
    """One page of the trash."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    count: Annotated[int, Field(description="How many entries the trash holds in total.")]
    page: int
    next_page: Annotated[int | None, Field(description="The next page, null on the last one.")]
    entries: tuple[TrashedDocument, ...]


class TrashRestored(BaseModel):
    """Entries that were taken out of the trash."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    restored: Annotated[
        tuple[TrashedDocument, ...], Field(description="The entries as they were in the trash.")
    ]
    message: Annotated[str, Field(description="What happened to documents and versions.")]


class TrashEmptied(BaseModel):
    """Entries that were removed from the trash for good."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    document_ids: tuple[int, ...]
    count: int
    message: str
