"""What Paperless AI reports: chat answers and suggestions proposed by the language model.

``ChatAnswer`` and ``AiSuggestions`` are what the port returns (ids, as Paperless sends them);
``AiAnswer`` and ``AiSuggestionReport`` are what the tools return (names and links).
"""

from typing import Annotated, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from paperless_mcp.domain.documents import Ref

# Output models are tool output schemas: a one-line class docstring each, and field descriptions
# only where the name does not say enough (they reach the LLM in every tools/list).


class _Frozen(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)


class ChatSource(_Frozen):
    """A document the answer drew on, as the chat named it."""

    id: int
    title: str


class ChatAnswer(_Frozen):
    """The complete answer of the Paperless chat.

    Attributes:
        answer: The answer text, without the metadata trailer the stream ends with.
        truncated: The stream was cut at the size or time limit; the answer is partial.
        sources: The documents the chat named as its sources (at most three).
    """

    answer: str
    truncated: bool
    sources: tuple[ChatSource, ...]


class AiSuggestions(_Frozen):
    """The suggestions of the language model: existing objects as ids, new ones as names."""

    title: str | None
    correspondents: tuple[int, ...]
    new_correspondents: tuple[str, ...]
    tags: tuple[int, ...]
    new_tags: tuple[str, ...]
    document_types: tuple[int, ...]
    new_document_types: tuple[str, ...]
    storage_paths: tuple[int, ...]
    new_storage_paths: tuple[str, ...]
    dates: tuple[str, ...]


class AiSource(_Frozen):
    """A document an answer drew on."""

    id: int
    title: str
    web_url: str


class AiAnswer(_Frozen):
    """The answer of the Paperless AI chat and the documents it drew on."""

    answer: str
    truncated: Annotated[
        bool, Field(description="True if the answer was cut at the size or time limit.")
    ]
    sources: Annotated[
        tuple[AiSource, ...],
        Field(description="Documents the answer drew on, as Paperless named them; may be empty."),
    ]


class AiSuggestionReport(_Frozen):
    """What the language model suggests for a document; ids come with names."""

    document_id: int
    web_url: str
    title: Annotated[str | None, Field(description="A suggested title; null if none.")]
    correspondents: tuple[Ref, ...]
    tags: tuple[Ref, ...]
    document_types: tuple[Ref, ...]
    storage_paths: tuple[Ref, ...]
    new_correspondents: Annotated[
        tuple[str, ...], Field(description="Names suggested that do not exist yet.")
    ]
    new_tags: tuple[str, ...]
    new_document_types: tuple[str, ...]
    new_storage_paths: tuple[str, ...]
    dates: tuple[str, ...]
