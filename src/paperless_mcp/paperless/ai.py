r"""AiReader on ``POST /api/documents/chat/`` and ``GET /api/documents/{id}/ai_suggestions/``.

The chat streams ``text/event-stream`` (3.4.2 ``paperless_ai.chat``): the answer as plain text
chunks, then, if documents were found, ``\n\n__PAPERLESS_CHAT_METADATA__`` and a JSON object with
the ``references`` (at most three documents). Failures while generating arrive in the body, as a
fixed sentence with status 200. The chat takes one optional ``document_id``; without it, it
searches every document the user may see. Both endpoints answer 400 ``AI is required for this
feature`` (text/html) while AI is switched off, which the error mapping turns into
``FeatureUnavailable``. ``ai_suggestions`` needs the ``change_document`` permission.
"""

from typing import TYPE_CHECKING

from pydantic import BaseModel, ValidationError

from paperless_mcp.domain.ai import AiSuggestions, ChatAnswer, ChatSource
from paperless_mcp.domain.errors import NotFound, UpstreamUnavailable, Validation
from paperless_mcp.paperless.documents import unseen_document

if TYPE_CHECKING:
    from paperless_mcp.paperless.client import PaperlessClient

CHAT_PATH = "/api/documents/chat/"
DOCUMENTS_PATH = "/api/documents/"
# What follows the answer in the stream: this delimiter, then the references as JSON.
METADATA_DELIMITER = "\n\n__PAPERLESS_CHAT_METADATA__"
# The whole body of the stream when generating the answer failed (the status is still 200).
CHAT_ERROR = "Sorry, something went wrong while generating a response."
# Largest answer kept (a long answer is far below it), and the most seconds one chat or one
# suggestion may take: retrieval and generation on a local model are slow.
CHAT_MAX_BYTES = 64 * 1024
CHAT_MAX_SECONDS = 120.0
AI_TIMEOUT = 120.0


class _Reference(BaseModel):
    id: int
    title: str


class _Trailer(BaseModel):
    references: list[_Reference]


class _Suggestions(BaseModel):
    title: str | None
    correspondents: list[int]
    suggested_correspondents: list[str]
    tags: list[int]
    suggested_tags: list[str]
    document_types: list[int]
    suggested_document_types: list[str]
    storage_paths: list[int]
    suggested_storage_paths: list[str]
    dates: list[str]


def _sources(trailer: str) -> tuple[ChatSource, ...]:
    """The references of the metadata trailer; none if it cannot be read."""
    try:
        found = _Trailer.model_validate_json(trailer)
    except ValidationError:
        return ()
    return tuple(ChatSource(id=ref.id, title=ref.title) for ref in found.references)


class PaperlessAiReader:
    """``AiReader`` over the Paperless REST API."""

    def __init__(
        self,
        client: PaperlessClient,
        *,
        max_bytes: int = CHAT_MAX_BYTES,
        max_seconds: float = CHAT_MAX_SECONDS,
    ) -> None:
        """Read through ``client``; an answer is cut at ``max_bytes`` or after ``max_seconds``."""
        self._client: PaperlessClient = client
        self._max_bytes: int = max_bytes
        self._max_seconds: float = max_seconds

    async def ask(self, question: str, *, document_id: int | None) -> ChatAnswer:
        """See ``AiReader.ask``.

        3.4.2 answers 400 "Document not found" to an unknown document and 403 to one the user
        may not see; the first one is the only 400 a valid question can get besides disabled AI.
        """
        body: dict[str, object] = {"q": question}
        if document_id is not None:
            body["document_id"] = document_id
        try:
            streamed = await self._client.stream_post(
                CHAT_PATH, json=body, max_bytes=self._max_bytes, max_seconds=self._max_seconds
            )
        except Validation:
            if document_id is None:
                raise
            raise unseen_document(document_id) from None
        text = streamed.content.decode(errors="replace")
        answer, _, trailer = text.partition(METADATA_DELIMITER)
        if answer.strip() == CHAT_ERROR:
            message = "Paperless AI could not generate a response."
            hint = "Ask again; if it keeps failing, check the LLM backend and the Paperless log."
            raise UpstreamUnavailable(message, hint=hint)
        return ChatAnswer(answer=answer, truncated=streamed.truncated, sources=_sources(trailer))

    async def get_ai_suggestions(self, document_id: int) -> AiSuggestions:
        """See ``AiReader.get_ai_suggestions``."""
        path = f"{DOCUMENTS_PATH}{document_id}/ai_suggestions/"
        try:
            row = await self._client.get(path, _Suggestions, timeout=AI_TIMEOUT)
        except NotFound:
            raise unseen_document(document_id) from None
        return AiSuggestions(
            title=row.title,
            correspondents=tuple(row.correspondents),
            new_correspondents=tuple(row.suggested_correspondents),
            tags=tuple(row.tags),
            new_tags=tuple(row.suggested_tags),
            document_types=tuple(row.document_types),
            new_document_types=tuple(row.suggested_document_types),
            storage_paths=tuple(row.storage_paths),
            new_storage_paths=tuple(row.suggested_storage_paths),
            dates=tuple(row.dates),
        )
