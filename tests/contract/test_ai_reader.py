"""Contract of AiReader against the fake and live Paperless 3.4.2.

Two groups. The first runs on the `backend` fixture: the live stack of CI has Paperless AI
switched off, so these pin what every AI endpoint answers then (a feature error, and the
permission and existence checks that come before it). The second runs on `ai_backend`: the
fake with AI on, and, in `just e2e-ai` only, a live Paperless with a developer-provided LLM
backend (the live parameter skips on a stack without AI). Their assertions hold for any
language model: shape, visibility and existence, never the wording of an answer.
"""

from typing import TYPE_CHECKING

import pytest
from fakes.paperless import FakeDocument, FakePaperless, seeded

from paperless_mcp.domain.ai import AiSuggestions
from paperless_mcp.domain.documents import DocumentQuery
from paperless_mcp.domain.errors import FeatureUnavailable, NotFound, PermissionDenied

if TYPE_CHECKING:
    from contract.conftest import Backend, Ports
    from paperless_mcp.domain.taxonomy import TaxonomyKind

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-8")]

ASK = "AiReader.ask"
SUGGEST = "AiReader.get_ai_suggestions"
PUBLIC, PRIVATE = "e2e-public-invoice", "e2e-private-note"
MISSING = 987_654_321
QUESTION = "What does the e2e public invoice from ACME say?"


async def document_id(ports: Ports, title: str) -> int:
    page = await ports.documents.search_documents(
        DocumentQuery(title_contains=title), page=1, page_size=10
    )
    [hit] = [hit for hit in page.hits if hit.title == title]
    return hit.id


# --- With AI switched off (the live stack of CI) ------------------------------------------------


@pytest.mark.contract(ASK)
async def test_asking_with_ai_off_is_a_feature_error(backend: Backend) -> None:
    for ports in (backend.admin, backend.reader):
        with pytest.raises(FeatureUnavailable, match="not enabled"):
            _ = await ports.ai.ask(QUESTION, document_id=None)


@pytest.mark.contract(ASK)
async def test_ai_off_is_reported_before_the_document_is_looked_up(backend: Backend) -> None:
    with pytest.raises(FeatureUnavailable):
        _ = await backend.admin.ai.ask(QUESTION, document_id=MISSING)


@pytest.mark.contract(ASK)
async def test_a_user_who_may_not_view_documents_cannot_ask(backend: Backend) -> None:
    with pytest.raises(PermissionDenied):
        _ = await backend.blind.ai.ask(QUESTION, document_id=None)


@pytest.mark.contract(SUGGEST)
async def test_ai_suggestions_with_ai_off_are_a_feature_error(backend: Backend) -> None:
    public = await document_id(backend.reader, PUBLIC)

    with pytest.raises(FeatureUnavailable, match="not enabled"):
        _ = await backend.reader.ai.get_ai_suggestions(public)


@pytest.mark.contract(SUGGEST)
async def test_ai_suggestions_of_a_missing_or_hidden_document(backend: Backend) -> None:
    private = await document_id(backend.admin, PRIVATE)

    with pytest.raises(NotFound):
        _ = await backend.reader.ai.get_ai_suggestions(MISSING)
    with pytest.raises(PermissionDenied):
        _ = await backend.reader.ai.get_ai_suggestions(private)


# --- With AI on (the fake; a live Paperless in `just e2e-ai`) -----------------------------------


@pytest.mark.contract(ASK)
async def test_an_answer_names_only_documents_the_user_may_see(ai_backend: Backend) -> None:
    visible = {
        hit.id
        for hit in (
            await ai_backend.reader.documents.search_documents(
                DocumentQuery(title_contains="e2e-"), page=1, page_size=100
            )
        ).hits
    }

    answer = await ai_backend.reader.ai.ask(QUESTION, document_id=None)

    assert answer.answer.strip()
    assert answer.truncated is False
    assert len(answer.sources) <= 3
    assert {source.id for source in answer.sources} <= visible


@pytest.mark.contract(ASK)
async def test_a_question_about_one_document_draws_on_that_document_only(
    ai_backend: Backend,
) -> None:
    public = await document_id(ai_backend.reader, PUBLIC)

    answer = await ai_backend.reader.ai.ask(QUESTION, document_id=public)

    assert answer.answer.strip()
    assert {source.id for source in answer.sources} <= {public}


@pytest.mark.contract(ASK)
async def test_asking_about_a_missing_or_hidden_document(ai_backend: Backend) -> None:
    private = await document_id(ai_backend.admin, PRIVATE)

    with pytest.raises(NotFound):
        _ = await ai_backend.reader.ai.ask(QUESTION, document_id=MISSING)
    with pytest.raises(PermissionDenied):
        _ = await ai_backend.reader.ai.ask(QUESTION, document_id=private)


@pytest.mark.contract(SUGGEST)
async def test_ai_suggestions_refer_to_existing_objects(ai_backend: Backend) -> None:
    public = await document_id(ai_backend.reader, PUBLIC)

    found = await ai_backend.reader.ai.get_ai_suggestions(public)

    assert isinstance(found, AiSuggestions)
    kinds: tuple[tuple[TaxonomyKind, tuple[int, ...]], ...] = (
        ("tag", found.tags),
        ("correspondent", found.correspondents),
        ("document_type", found.document_types),
        ("storage_path", found.storage_paths),
    )
    for kind, ids in kinds:
        known = {item.id for item in await ai_backend.reader.taxonomy.list_taxonomy(kind)}
        assert set(ids) <= known, kind


@pytest.mark.contract(SUGGEST)
async def test_ai_suggestions_of_a_hidden_document_are_denied_with_ai_on(
    ai_backend: Backend,
) -> None:
    private = await document_id(ai_backend.admin, PRIVATE)

    with pytest.raises(PermissionDenied):
        _ = await ai_backend.reader.ai.get_ai_suggestions(private)


# --- What only the fake can show: the fake itself is what services and tools are tested on ------


async def test_the_fake_answers_from_the_documents_that_match_the_question() -> None:
    state = seeded()
    state.ai_enabled = True
    state.documents.append(
        FakeDocument(id=9, title="Lease", content="The rent is 900 euros a month")
    )
    fake = FakePaperless(state, user="admin")

    matched = await fake.ask("How much is the rent?", document_id=None)
    unmatched = await fake.ask("zebra", document_id=None)

    assert [source.id for source in matched.sources] == [9]
    assert "Lease" in matched.answer
    assert unmatched.sources == ()


async def test_the_fake_returns_the_suggestions_set_on_the_document() -> None:
    state = seeded()
    state.ai_enabled = True
    suggested = AiSuggestions(
        title="A title",
        correspondents=(1,),
        new_correspondents=("Acme Ltd",),
        tags=(2,),
        new_tags=(),
        document_types=(),
        new_document_types=(),
        storage_paths=(),
        new_storage_paths=(),
        dates=("2026-01-15",),
    )
    state.documents[0].ai_suggestions = suggested

    assert await FakePaperless(state, user="admin").get_ai_suggestions(1) == suggested
