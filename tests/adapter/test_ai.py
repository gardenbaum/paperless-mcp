"""PaperlessAiReader on the AI endpoints of Paperless 3.4.2 (chat, AI suggestions).

The stack of CI runs with AI switched off, so the answers of a working AI backend are built from
the 3.4.2 source (``ChatStreamingView``, ``paperless_ai.chat``, the ``ai_suggestions`` action);
requests and the documented responses go through the schema guard.
"""

import json
from typing import TYPE_CHECKING, cast, override

import httpx2
import pytest

from paperless_mcp.domain.ai import AiSuggestions, ChatAnswer, ChatSource
from paperless_mcp.domain.errors import (
    FeatureUnavailable,
    NotFound,
    PermissionDenied,
    UpstreamUnavailable,
    Validation,
)
from paperless_mcp.paperless.ai import (
    AI_TIMEOUT,
    CHAT_ERROR,
    CHAT_MAX_BYTES,
    CHAT_MAX_SECONDS,
    METADATA_DELIMITER,
    PaperlessAiReader,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from adapter.conftest import ClientFactory

pytestmark = pytest.mark.anyio

type Json = dict[str, object]

AI_OFF = httpx2.Response(
    400, text="AI is required for this feature", headers={"Content-Type": "text/html"}
)


def trailer(*references: Json) -> str:
    return METADATA_DELIMITER + json.dumps({"references": list(references)}, separators=(",", ":"))


class Pieces(httpx2.AsyncByteStream):
    """A text/event-stream body that arrives in pieces."""

    def __init__(self, *pieces: str) -> None:
        self.pieces: tuple[str, ...] = pieces

    @override
    async def __aiter__(self) -> AsyncIterator[bytes]:
        for piece in self.pieces:
            yield piece.encode()


def stream(*pieces: str, status: int = 200) -> httpx2.Response:
    return httpx2.Response(
        status, headers={"Content-Type": "text/event-stream"}, stream=Pieces(*pieces)
    )


class Chat:
    """Answers the chat endpoint with a fixed response and records the requests."""

    def __init__(self, response: httpx2.Response) -> None:
        self.response: httpx2.Response = response
        self.sent: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.sent.append(request)
        return self.response

    @property
    def bodies(self) -> list[object]:
        return [cast("object", json.loads(r.content)) for r in self.sent]


async def test_a_question_goes_to_the_chat_endpoint_and_the_answer_comes_back_whole(
    make_client: ClientFactory,
) -> None:
    server = Chat(stream("The invoice ", "is due on ", "1 March."))

    found = await PaperlessAiReader(make_client(server)).ask("When is it due?", document_id=None)

    assert [(r.method, r.url.path) for r in server.sent] == [("POST", "/api/documents/chat/")]
    assert server.bodies == [{"q": "When is it due?"}]
    assert found == ChatAnswer(answer="The invoice is due on 1 March.", truncated=False, sources=())


async def test_a_question_about_one_document_names_it(make_client: ClientFactory) -> None:
    server = Chat(stream("Yes."))

    _ = await PaperlessAiReader(make_client(server)).ask("Paid?", document_id=7)

    assert server.bodies == [{"q": "Paid?", "document_id": 7}]


async def test_the_sources_come_from_the_metadata_trailer(make_client: ClientFactory) -> None:
    references: list[Json] = [{"id": 3, "title": "Invoice 3"}, {"id": 9, "title": "Offer"}]
    server = Chat(stream("Due ", "soon.", trailer(*references)))

    found = await PaperlessAiReader(make_client(server)).ask("When?", document_id=None)

    assert found.answer == "Due soon."
    assert found.sources == (ChatSource(id=3, title="Invoice 3"), ChatSource(id=9, title="Offer"))


@pytest.mark.parametrize(
    "tail",
    [
        METADATA_DELIMITER + "not json",
        METADATA_DELIMITER + '{"references": "none"}',
        METADATA_DELIMITER + '{"references": [{"id": "x"}]}',
        METADATA_DELIMITER,
    ],
)
async def test_a_trailer_that_cannot_be_read_costs_the_sources_only(
    make_client: ClientFactory, tail: str
) -> None:
    server = Chat(stream("Due soon.", tail))

    found = await PaperlessAiReader(make_client(server)).ask("When?", document_id=None)

    assert (found.answer, found.sources, found.truncated) == ("Due soon.", (), False)


async def test_a_delimiter_split_over_two_pieces_is_still_found(
    make_client: ClientFactory,
) -> None:
    whole = "Done." + trailer({"id": 1, "title": "One"})
    server = Chat(stream(whole[:10], whole[10:]))

    found = await PaperlessAiReader(make_client(server)).ask("?", document_id=None)

    assert (found.answer, found.sources) == ("Done.", (ChatSource(id=1, title="One"),))


async def test_non_ascii_text_is_decoded_as_utf8(make_client: ClientFactory) -> None:
    server = Chat(stream("Rechnung über 12 €"))

    found = await PaperlessAiReader(make_client(server)).ask("?", document_id=None)

    assert found.answer == "Rechnung über 12 €"


async def test_the_sentence_paperless_sends_when_generation_fails_is_an_error(
    make_client: ClientFactory,
) -> None:
    # stream_chat_with_documents yields CHAT_ERROR_MESSAGE as the whole body after an exception
    server = Chat(stream(CHAT_ERROR))

    with pytest.raises(UpstreamUnavailable) as raised:
        _ = await PaperlessAiReader(make_client(server)).ask("?", document_id=None)

    assert raised.value.message == "Paperless AI could not generate a response."
    assert raised.value.hint == (
        "Ask again; if it keeps failing, check the LLM backend and the Paperless log."
    )


async def test_the_no_content_sentence_is_an_ordinary_answer(make_client: ClientFactory) -> None:
    sentence = "Sorry, I couldn't find any content to answer your question."
    server = Chat(stream(sentence))

    found = await PaperlessAiReader(make_client(server)).ask("?", document_id=None)

    assert found.answer == sentence


async def test_an_answer_past_the_size_limit_is_cut_and_marked(make_client: ClientFactory) -> None:
    server = Chat(stream("0123456789", "abcdefghij"))
    reader = PaperlessAiReader(make_client(server), max_bytes=12)

    found = await reader.ask("?", document_id=None)

    assert (found.answer, found.truncated, found.sources) == ("0123456789ab", True, ())


async def test_the_time_limit_is_sent_as_the_request_timeout(make_client: ClientFactory) -> None:
    server = Chat(stream("ok"))

    _ = await PaperlessAiReader(make_client(server), max_seconds=41).ask("?", document_id=None)

    assert cast("dict[str, float]", server.sent[0].extensions["timeout"])["read"] == 41


def test_the_limits_are_bounded() -> None:
    assert (CHAT_MAX_BYTES, CHAT_MAX_SECONDS, AI_TIMEOUT) == (64 * 1024, 120.0, 120.0)


async def test_disabled_ai_is_a_feature_error(make_client: ClientFactory) -> None:
    with pytest.raises(FeatureUnavailable, match="not enabled"):
        _ = await PaperlessAiReader(make_client(Chat(AI_OFF))).ask("?", document_id=None)


async def test_an_unknown_document_is_not_found(make_client: ClientFactory) -> None:
    # 3.4.2: HttpResponseBadRequest("Document not found")
    missing = httpx2.Response(400, text="Document not found", headers={"Content-Type": "text/html"})

    with pytest.raises(NotFound, match="No document 9"):
        _ = await PaperlessAiReader(make_client(Chat(missing))).ask("?", document_id=9)


async def test_a_document_the_user_may_not_see_is_permission_denied(
    make_client: ClientFactory,
) -> None:
    forbidden = httpx2.Response(
        403, text="Insufficient permissions", headers={"Content-Type": "text/html"}
    )

    with pytest.raises(PermissionDenied):
        _ = await PaperlessAiReader(make_client(Chat(forbidden))).ask("?", document_id=9)


async def test_the_chat_is_not_retried(make_client: ClientFactory) -> None:
    server = Chat(httpx2.Response(503, json={"detail": "busy"}))

    with pytest.raises(UpstreamUnavailable):
        _ = await PaperlessAiReader(make_client(server)).ask("?", document_id=None)

    assert len(server.sent) == 1


SUGGESTIONS: Json = {
    "title": "Invoice March 2026",
    "correspondents": [1],
    "suggested_correspondents": ["Acme Ltd"],
    "tags": [2, 3],
    "suggested_tags": [],
    "document_types": [],
    "suggested_document_types": ["Invoice"],
    "storage_paths": [4],
    "suggested_storage_paths": [],
    "dates": ["2026-03-01"],
}


class Suggestions:
    def __init__(self, response: httpx2.Response) -> None:
        self.response: httpx2.Response = response
        self.sent: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.sent.append(request)
        return self.response


async def test_ai_suggestions_are_read_from_the_ai_endpoint_with_a_long_timeout(
    make_client: ClientFactory,
) -> None:
    server = Suggestions(httpx2.Response(200, json=SUGGESTIONS))

    found = await PaperlessAiReader(make_client(server)).get_ai_suggestions(5)

    [request] = server.sent
    assert request.url.path == "/api/documents/5/ai_suggestions/"
    assert cast("dict[str, float]", request.extensions["timeout"])["read"] == AI_TIMEOUT
    assert found == AiSuggestions(
        title="Invoice March 2026",
        correspondents=(1,),
        new_correspondents=("Acme Ltd",),
        tags=(2, 3),
        new_tags=(),
        document_types=(),
        new_document_types=("Invoice",),
        storage_paths=(4,),
        new_storage_paths=(),
        dates=("2026-03-01",),
    )


async def test_a_suggestion_without_a_title_has_none(make_client: ClientFactory) -> None:
    server = Suggestions(httpx2.Response(200, json={**SUGGESTIONS, "title": None}))

    found = await PaperlessAiReader(make_client(server)).get_ai_suggestions(5)

    assert found.title is None


async def test_ai_suggestions_of_a_missing_document_are_not_found(
    make_client: ClientFactory,
) -> None:
    server = Suggestions(httpx2.Response(404, json={"detail": "No Document matches."}))

    with pytest.raises(NotFound, match="No document 9"):
        _ = await PaperlessAiReader(make_client(server)).get_ai_suggestions(9)


async def test_ai_suggestions_the_user_may_not_read_are_permission_denied(
    make_client: ClientFactory,
) -> None:
    # 3.4.2 checks change_document before it looks at AI: HttpResponseForbidden, text/html
    server = Suggestions(
        httpx2.Response(403, text="Insufficient permissions", headers={"Content-Type": "text/html"})
    )

    with pytest.raises(PermissionDenied):
        _ = await PaperlessAiReader(make_client(server)).get_ai_suggestions(1)


async def test_ai_suggestions_with_ai_disabled_are_a_feature_error(
    make_client: ClientFactory,
) -> None:
    with pytest.raises(FeatureUnavailable, match="not enabled"):
        _ = await PaperlessAiReader(make_client(Suggestions(AI_OFF))).get_ai_suggestions(1)


async def test_a_backend_that_rejects_the_request_is_unavailable(
    make_client: ClientFactory,
) -> None:
    # 3.4.2: 502 {"ai": ["AI backend rejected the request. Check logs for details."]}
    server = Suggestions(httpx2.Response(502, json={"ai": ["AI backend rejected the request."]}))

    with pytest.raises(UpstreamUnavailable, match="HTTP 502"):
        _ = await PaperlessAiReader(make_client(server)).get_ai_suggestions(1)


async def test_a_rejected_question_without_a_document_stays_a_validation_error(
    make_client: ClientFactory,
) -> None:
    # not an unknown document: there is none to blame
    server = Chat(
        httpx2.Response(400, text="Invalid request", headers={"Content-Type": "text/html"})
    )

    with pytest.raises(Validation):
        _ = await PaperlessAiReader(make_client(server)).ask("?", document_id=None)


async def test_bytes_that_are_not_utf8_are_replaced_not_fatal(make_client: ClientFactory) -> None:
    body = httpx2.Response(
        200, headers={"Content-Type": "text/event-stream"}, content=b"ok \xff end"
    )

    found = await PaperlessAiReader(make_client(Chat(body))).ask("?", document_id=None)

    assert found.answer == "ok \ufffd end"


async def test_ai_suggestions_are_never_retried(make_client: ClientFactory) -> None:
    # every attempt is a model call that may cost money
    server = Suggestions(httpx2.Response(503, json={"ai": ["AI backend request timed out."]}))

    with pytest.raises(UpstreamUnavailable):
        _ = await PaperlessAiReader(make_client(server)).get_ai_suggestions(1)

    assert len(server.sent) == 1


# --- PLMCP-20 review


async def test_the_failure_sentence_after_part_of_an_answer_is_still_an_error(
    make_client: ClientFactory,
) -> None:
    # the generator can fail midway: the sentence is appended to what was already streamed.
    # Only this exact wording is recognised; if Paperless rewords it the text reads as an answer.
    server = Chat(stream("The invoice is ", CHAT_ERROR))

    with pytest.raises(UpstreamUnavailable, match="could not generate"):
        _ = await PaperlessAiReader(make_client(server)).ask("?", document_id=None)


async def test_a_cut_inside_the_metadata_delimiter_leaves_no_delimiter_stub(
    make_client: ClientFactory,
) -> None:
    server = Chat(stream("Answer" + METADATA_DELIMITER))
    reader = PaperlessAiReader(make_client(server), max_bytes=len("Answer") + 9)

    found = await reader.ask("?", document_id=None)

    assert (found.answer, found.truncated) == ("Answer", True)


async def test_text_that_merely_ends_like_the_delimiter_is_kept_when_not_truncated(
    make_client: ClientFactory,
) -> None:
    found = await PaperlessAiReader(make_client(Chat(stream("Done.\n")))).ask("?", document_id=None)

    assert (found.answer, found.truncated) == ("Done.\n", False)


async def test_another_400_for_a_question_about_a_document_stays_a_validation_error(
    make_client: ClientFactory,
) -> None:
    other = httpx2.Response(400, text="Invalid request", headers={"Content-Type": "text/html"})

    with pytest.raises(Validation):
        _ = await PaperlessAiReader(make_client(Chat(other))).ask("?", document_id=9)


async def test_not_found_without_a_document_is_passed_on(make_client: ClientFactory) -> None:
    missing = httpx2.Response(400, text="Document not found", headers={"Content-Type": "text/html"})

    with pytest.raises(NotFound, match="does not exist"):
        _ = await PaperlessAiReader(make_client(Chat(missing))).ask("?", document_id=None)


@pytest.mark.parametrize("cut", [1, len(METADATA_DELIMITER) - 1])
async def test_every_length_of_delimiter_stub_is_removed(
    make_client: ClientFactory, cut: int
) -> None:
    server = Chat(stream("Answer" + METADATA_DELIMITER[:cut] + "tail beyond the limit"))
    reader = PaperlessAiReader(make_client(server), max_bytes=len("Answer") + cut)

    found = await reader.ask("?", document_id=None)

    assert (found.answer, found.truncated) == ("Answer", True)


async def test_the_failure_sentence_is_found_before_trailing_whitespace(
    make_client: ClientFactory,
) -> None:
    server = Chat(stream("Part ", CHAT_ERROR + "\n"))

    with pytest.raises(UpstreamUnavailable):
        _ = await PaperlessAiReader(make_client(server)).ask("?", document_id=None)
