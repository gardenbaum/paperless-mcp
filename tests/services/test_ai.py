"""AiService: questions checked before they cost a model call; answers and suggestions."""

from typing import TYPE_CHECKING, Never, override

import pytest
from fakes.accounts import ADMIN, READER
from fakes.paperless import FakePaperless, FakeState, FakeUser, seeded

from paperless_mcp.domain.ai import AiSource, AiSuggestions, ChatAnswer, ChatSource
from paperless_mcp.domain.documents import Ref
from paperless_mcp.domain.errors import FeatureUnavailable, NotFound, PermissionDenied, Validation
from paperless_mcp.paperless.auth import StaticTokenProvider
from paperless_mcp.services.ai import MAX_QUESTION, AiService
from paperless_mcp.services.documents import document_link
from paperless_mcp.services.taxonomy import TaxonomyResolver

if TYPE_CHECKING:
    from paperless_mcp.domain.taxonomy import TaxonomyKind

pytestmark = pytest.mark.anyio

WEB = "https://paperless.example/"


def fake(user: str = ADMIN[0], *, ai_enabled: bool = True) -> FakePaperless:
    state = seeded()
    state.ai_enabled = ai_enabled
    return FakePaperless(state, user=user)


def service(paperless: FakePaperless) -> AiService:
    resolver = TaxonomyResolver(paperless, StaticTokenProvider("t"), ttl=60.0)
    return AiService(paperless, resolver, web_url=WEB)


class Counting(FakePaperless):
    """The fake, recording the questions that reach Paperless."""

    def __init__(self, state: FakeState) -> None:
        super().__init__(state, user=ADMIN[0])
        self.questions: list[tuple[str, int | None]] = []

    @override
    async def ask(self, question: str, *, document_id: int | None) -> ChatAnswer:
        self.questions.append((question, document_id))
        return await super().ask(question, document_id=document_id)


def counting() -> Counting:
    state = seeded()
    state.ai_enabled = True
    return Counting(state)


async def test_an_answer_is_returned_with_links_to_its_sources() -> None:
    answer = await service(fake()).ask("acme", document_id=None)

    assert answer.truncated is False
    assert "e2e-public-invoice" in answer.answer
    assert answer.sources == (
        AiSource(id=1, title="e2e-public-invoice", web_url=document_link(WEB, 1)),
    )


async def test_a_truncated_answer_says_so() -> None:
    class Cut(FakePaperless):
        @override
        async def ask(self, question: str, *, document_id: int | None) -> ChatAnswer:
            return ChatAnswer(
                answer="Half an", truncated=True, sources=(ChatSource(id=2, title="t"),)
            )

    state = seeded()
    answer = await service(Cut(state, user=ADMIN[0])).ask("anything", document_id=None)

    assert (answer.answer, answer.truncated) == ("Half an", True)
    assert [source.web_url for source in answer.sources] == [document_link(WEB, 2)]


async def test_the_question_for_one_document_reaches_paperless_as_asked() -> None:
    paperless = counting()

    _ = await service(paperless).ask("  e2e public invoice ", document_id=1)

    assert paperless.questions == [("e2e public invoice", 1)]


@pytest.mark.parametrize("question", ["", "   ", "\n\t"])
async def test_a_blank_question_is_rejected_before_paperless_is_asked(question: str) -> None:
    paperless = counting()

    with pytest.raises(Validation) as raised:
        _ = await service(paperless).ask(question, document_id=None)

    assert raised.value.message == "The question is empty."
    assert raised.value.hint == "Ask a question about the documents."
    assert paperless.questions == []


async def test_a_question_over_the_limit_is_rejected_before_paperless_is_asked() -> None:
    paperless = counting()

    with pytest.raises(Validation) as raised:
        _ = await service(paperless).ask("x" * (MAX_QUESTION + 1), document_id=None)
    assert raised.value.message == "The question has 4001 characters; the limit is 4000."
    assert raised.value.hint == "Shorten it, or ask one thing at a time."
    _ = await service(paperless).ask("x" * MAX_QUESTION, document_id=None)

    assert len(paperless.questions) == 1


async def test_errors_of_the_reader_reach_the_caller() -> None:
    with pytest.raises(FeatureUnavailable):
        _ = await service(fake(ai_enabled=False)).ask("anything", document_id=None)
    with pytest.raises(NotFound):
        _ = await service(fake()).ask("anything", document_id=999)


async def test_suggestions_come_with_names_and_the_link() -> None:
    paperless = fake()
    paperless.state.documents[0].ai_suggestions = AiSuggestions(
        title="Invoice",
        correspondents=(1,),
        new_correspondents=("Acme Ltd",),
        tags=(2, 77),
        new_tags=("urgent",),
        document_types=(1,),
        new_document_types=(),
        storage_paths=(1,),
        new_storage_paths=("Taxes",),
        dates=("2026-03-01",),
    )

    report = await service(paperless).suggest(1)

    assert report.document_id == 1
    assert report.web_url == document_link(WEB, 1)
    assert report.title == "Invoice"
    assert report.correspondents == (Ref(id=1, name="e2e-acme"),)
    assert report.tags == (Ref(id=2, name="e2e-invoices"), Ref(id=77, name=None))
    assert report.document_types == (Ref(id=1, name="e2e-invoice"),)
    assert report.storage_paths == (Ref(id=1, name="e2e-archive"),)
    assert report.new_correspondents == ("Acme Ltd",)
    assert report.new_tags == ("urgent",)
    assert report.new_document_types == ()
    assert report.new_storage_paths == ("Taxes",)
    assert report.dates == ("2026-03-01",)


async def test_a_kind_the_user_may_not_list_leaves_its_names_null() -> None:
    state = seeded()
    state.ai_enabled = True
    state.users.append(
        FakeUser(id=9, username="viewer", permissions=frozenset({"view_document", "view_tag"}))
    )
    state.documents[0].ai_suggestions = AiSuggestions(
        title=None,
        correspondents=(1,),
        new_correspondents=(),
        tags=(1,),
        new_tags=(),
        document_types=(),
        new_document_types=(),
        storage_paths=(),
        new_storage_paths=(),
        dates=(),
    )

    report = await service(FakePaperless(state, user="viewer")).suggest(1)

    assert report.correspondents == (Ref(id=1, name=None),)
    assert report.tags == (Ref(id=1, name="e2e-finance"),)


async def test_a_kind_without_suggestions_is_not_listed_at_all() -> None:
    class NoListing(FakePaperless):
        @override
        async def list_taxonomy(self, kind: TaxonomyKind) -> Never:
            raise AssertionError(kind)

    state = seeded()
    state.ai_enabled = True

    report = await service(NoListing(state, user=ADMIN[0])).suggest(1)

    assert (report.tags, report.correspondents, report.document_types) == ((), (), ())
    assert report.storage_paths == ()


async def test_suggestion_errors_reach_the_caller() -> None:
    with pytest.raises(FeatureUnavailable):
        _ = await service(fake(ai_enabled=False)).suggest(1)
    with pytest.raises(NotFound):
        _ = await service(fake(READER[0])).suggest(999)
    with pytest.raises(PermissionDenied):
        _ = await service(fake(READER[0])).suggest(2)
