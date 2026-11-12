"""Paperless AI: questions answered over the documents, and the model's suggestions for one.

Both ask a language model through Paperless, which is slow and may cost money, so a question
that cannot be answered is refused here before it is sent. Suggestions arrive as ids (existing
objects) and names (new ones); ids are shown with the names of the objects the user may list.
"""

from typing import TYPE_CHECKING, Final

from paperless_mcp.domain.ai import AiAnswer, AiSource, AiSuggestionReport
from paperless_mcp.domain.documents import Ref
from paperless_mcp.domain.errors import PermissionDenied, Validation
from paperless_mcp.services.documents import document_link

if TYPE_CHECKING:
    from paperless_mcp.domain.ports import AiReader
    from paperless_mcp.domain.taxonomy import TaxonomyKind
    from paperless_mcp.services.taxonomy import TaxonomyResolver

# The longest question the Paperless chat accepts (ChatStreamingRequest.q maxLength).
MAX_QUESTION: Final = 4000


class AiService:
    """Asks Paperless AI on behalf of the current user."""

    def __init__(self, reader: AiReader, taxonomy: TaxonomyResolver, *, web_url: str) -> None:
        """Ask through ``reader``; ``web_url`` is the base of links to the Paperless UI."""
        self._reader: AiReader = reader
        self._taxonomy: TaxonomyResolver = taxonomy
        self._web_url: str = web_url

    async def ask(self, question: str, *, document_id: int | None) -> AiAnswer:
        """The complete answer to ``question``, about one document or all the user may see.

        Raises:
            Validation: The question is blank or longer than Paperless accepts.
            FeatureUnavailable: Paperless AI is switched off.
            NotFound: ``document_id`` names no document.
            PermissionDenied: The user may not view documents, or not this document.
            UpstreamUnavailable: The language model failed or took too long.
        """
        text = question.strip()
        if not text:
            message = "The question is empty."
            raise Validation(message, hint="Ask a question about the documents.")
        if len(text) > MAX_QUESTION:
            message = f"The question has {len(text)} characters; the limit is {MAX_QUESTION}."
            raise Validation(message, hint="Shorten it, or ask one thing at a time.")
        found = await self._reader.ask(text, document_id=document_id)
        return AiAnswer(
            answer=found.answer,
            truncated=found.truncated,
            sources=tuple(
                AiSource(
                    id=source.id,
                    title=source.title,
                    web_url=document_link(self._web_url, source.id),
                )
                for source in found.sources
            ),
        )

    async def suggest(self, document_id: int) -> AiSuggestionReport:
        """What the language model suggests for a document, with the names of existing objects.

        Raises:
            NotFound: No such document.
            PermissionDenied: The user may not change the document (Paperless requires it).
            FeatureUnavailable: Paperless AI is switched off.
            UpstreamUnavailable: The language model failed or took too long.
        """
        found = await self._reader.get_ai_suggestions(document_id)
        return AiSuggestionReport(
            document_id=document_id,
            web_url=document_link(self._web_url, document_id),
            title=found.title,
            correspondents=await self._refs("correspondent", found.correspondents),
            tags=await self._refs("tag", found.tags),
            document_types=await self._refs("document_type", found.document_types),
            storage_paths=await self._refs("storage_path", found.storage_paths),
            new_correspondents=found.new_correspondents,
            new_tags=found.new_tags,
            new_document_types=found.new_document_types,
            new_storage_paths=found.new_storage_paths,
            dates=found.dates,
        )

    async def _refs(self, kind: TaxonomyKind, numbers: tuple[int, ...]) -> tuple[Ref, ...]:
        """The objects with their names; a name is null if the user may not list the kind."""
        if not numbers:
            return ()
        try:
            names = {item.id: item.name for item in await self._taxonomy.items(kind)}
        except PermissionDenied:
            names = {}
        return tuple(Ref(id=number, name=names.get(number)) for number in numbers)
