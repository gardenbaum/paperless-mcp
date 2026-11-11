"""TaxonomyResolver: name-or-id resolution (decision-5) and its per-credential cache."""

import unicodedata
from typing import TYPE_CHECKING

import pytest
from fakes.accounts import ADMIN, READER
from fakes.paperless import FakePaperless, FakeTag, FakeTaxonomy, seeded
from hypothesis import assume, example, given
from hypothesis import strategies as st

from paperless_mcp.domain.credentials import PaperlessCredential
from paperless_mcp.domain.errors import Ambiguous, NotFound
from paperless_mcp.domain.taxonomy import TaxonomyItem
from paperless_mcp.services.taxonomy import TaxonomyResolver, fold, pick

if TYPE_CHECKING:
    from paperless_mcp.domain.taxonomy import TaxonomyKind

pytestmark = pytest.mark.decision("decision-5")

TTL = 60.0


def items(*names: str) -> list[TaxonomyItem]:
    return [
        TaxonomyItem(id=index, name=name, document_count=0)
        for index, name in enumerate(names, start=1)
    ]


names = st.text(alphabet=st.characters(codec="utf-8", exclude_categories=["Cs"]), max_size=12)
# Names that a digit-only query cannot be confused with: numeric input is always an id.
words = names.filter(lambda name: not name.strip().isdigit())


def test_fold_ignores_case_and_accents() -> None:
    assert fold("Ärztekammer") == fold("arztekammer") == fold("ARZTEKAMMER")
    assert fold("Straße") == fold("STRASSE")


@given(st.lists(words, max_size=6, unique=True), st.data())
def test_an_exact_name_wins_over_insensitive_matches(pool: list[str], data: st.DataObject) -> None:
    _ = assume(pool)
    name = data.draw(st.sampled_from(pool))
    _ = assume(name.strip() == name and name and name.swapcase() != name)

    assert pick("tag", items(*pool, name.swapcase()), name).name == name


@given(words, st.data())
def test_more_than_one_insensitive_match_is_always_ambiguous(
    name: str, data: st.DataObject
) -> None:
    variants = [name.upper(), name.lower(), name.title(), name.swapcase()]
    distinct = list(dict.fromkeys(v for v in variants if fold(v) == fold(name)))
    query = data.draw(st.sampled_from(variants))
    others = [v for v in distinct if v != query]
    _ = assume(len(others) >= 2 and query.strip() == query and fold(query))

    with pytest.raises(Ambiguous) as raised:
        _ = pick("tag", items(*others), query)

    assert all(f"{other!r} (id " in raised.value.message for other in others)


@example(pool=["a"], query=" a")
@given(st.lists(words, max_size=6), words)
def test_a_match_is_never_a_guess(pool: list[str], query: str) -> None:
    try:
        found = pick("tag", items(*pool), query)
    except NotFound, Ambiguous:
        return
    assert fold(found.name) == fold(query.strip())  # pick strips the input


@given(st.lists(words, max_size=6), st.integers(min_value=1, max_value=8))
def test_numeric_input_is_an_id(pool: list[str], wanted: int) -> None:
    rows = items(*pool)
    for value in (wanted, str(wanted), f" {wanted} "):
        if wanted <= len(rows):
            assert pick("tag", rows, value).id == wanted
        else:
            with pytest.raises(NotFound):
                _ = pick("tag", rows, value)


def test_a_digit_only_name_is_read_as_an_id_and_the_hint_names_its_id() -> None:
    with pytest.raises(NotFound) as raised:
        _ = pick("tag", items("Other", "2024"), "2024")

    assert raised.value.hint == "A tag is named '2024'; pass its id 2 instead."


def test_a_boolean_is_a_name_not_an_id() -> None:
    assert pick("tag", items("x", "True"), True).id == 2  # noqa: FBT003 -- the input under test


def test_no_match_suggests_the_closest_names() -> None:
    with pytest.raises(NotFound) as raised:
        _ = pick("document_type", items("Invoice", "Receipt", "Contract"), "Invoise")

    error = raised.value
    assert error.message == "No document type is named 'Invoise'."
    assert error.hint is not None
    assert "'Invoice'" in error.hint
    assert "Receipt" not in error.hint
    assert "list_taxonomy" in error.hint


def test_no_match_without_close_names_points_to_the_list() -> None:
    with pytest.raises(NotFound) as raised:
        _ = pick("tag", items("Invoice"), "zzz")

    assert raised.value.hint == "Call list_taxonomy(kind='tag') to see the names."


def test_ambiguity_names_the_candidates_and_asks_for_an_id() -> None:
    rows = [
        TaxonomyItem(id=3, name="Tax", document_count=0),
        TaxonomyItem(id=7, name="Tax", document_count=0),
    ]

    with pytest.raises(Ambiguous) as raised:
        _ = pick("tag", rows, "Tax")

    assert raised.value.message == "2 tags match 'Tax': 'Tax' (id 3), 'Tax' (id 7)."
    assert raised.value.hint == "Pass the id of the one you mean."


def test_unicode_variants_match_insensitively() -> None:
    decomposed = unicodedata.normalize("NFD", "Café")

    assert pick("tag", items(decomposed, "Other"), "cafe").name == decomposed


# --- The resolver on FakePaperless -------------------------------------------------------------


class Credentials:
    """A credential provider whose fingerprint the test switches."""

    def __init__(self, fingerprint: str = "admin") -> None:
        self.fingerprint: str = fingerprint

    def current(self) -> PaperlessCredential:
        return PaperlessCredential(
            header=("Authorization", "Token t"), fingerprint=self.fingerprint
        )


class PerCredential:
    """A TaxonomyReader that answers as the fake of the current credential and counts loads."""

    def __init__(self, credentials: Credentials, fakes: dict[str, FakePaperless]) -> None:
        self.credentials: Credentials = credentials
        self.fakes: dict[str, FakePaperless] = fakes
        self.loads: list[tuple[str, TaxonomyKind]] = []

    async def list_taxonomy(self, kind: TaxonomyKind) -> list[TaxonomyItem]:
        fingerprint = self.credentials.fingerprint
        self.loads.append((fingerprint, kind))
        return await self.fakes[fingerprint].list_taxonomy(kind)


class Clock:
    def __init__(self) -> None:
        self.now: float = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def credentials() -> Credentials:
    return Credentials()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def reader(credentials: Credentials) -> PerCredential:
    state = seeded()
    state.document_types.append(FakeTaxonomy(id=2, name="Tax"))
    state.tags.append(FakeTag(id=5, name="Tax"))
    return PerCredential(
        credentials,
        {
            "admin": FakePaperless(state, user=ADMIN[0]),
            "reader": FakePaperless(state, user=READER[0]),
        },
    )


@pytest.fixture
def resolver(reader: PerCredential, credentials: Credentials, clock: Clock) -> TaxonomyResolver:
    return TaxonomyResolver(reader, credentials, ttl=TTL, clock=clock)


@pytest.mark.anyio
async def test_the_same_name_resolves_independently_per_kind(resolver: TaxonomyResolver) -> None:
    tag = await resolver.resolve("tag", "tax")
    document_type = await resolver.resolve("document_type", "TAX")

    assert (tag.id, document_type.id) == (5, 2)


@pytest.mark.anyio
async def test_an_object_the_user_cannot_see_does_not_exist(
    resolver: TaxonomyResolver, credentials: Credentials
) -> None:
    credentials.fingerprint = "reader"

    for value in ("e2e-private", 3, "e2e-privat"):
        with pytest.raises(NotFound) as raised:
            _ = await resolver.resolve("tag", value)
        assert "e2e-private" not in (raised.value.hint or "")


@pytest.mark.anyio
async def test_the_cache_serves_one_load_per_kind_and_credential_within_the_ttl(
    resolver: TaxonomyResolver, reader: PerCredential, credentials: Credentials, clock: Clock
) -> None:
    _ = await resolver.resolve("tag", "e2e-finance")
    clock.now += TTL - 1
    _ = await resolver.items("tag")
    credentials.fingerprint = "reader"
    _ = await resolver.items("tag")
    _ = await resolver.items("correspondent")

    assert reader.loads == [("admin", "tag"), ("reader", "tag"), ("reader", "correspondent")]


@pytest.mark.anyio
async def test_the_cache_expires_after_the_ttl(
    resolver: TaxonomyResolver, reader: PerCredential, clock: Clock
) -> None:
    _ = await resolver.items("tag")
    clock.now += TTL
    _ = await resolver.items("tag")

    assert reader.loads == [("admin", "tag"), ("admin", "tag")]


@pytest.mark.anyio
async def test_invalidation_reloads_the_kind_for_every_credential(
    resolver: TaxonomyResolver, reader: PerCredential, credentials: Credentials
) -> None:
    _ = await resolver.items("tag")
    _ = await resolver.items("correspondent")
    credentials.fingerprint = "reader"
    _ = await resolver.items("tag")
    reader.fakes["admin"].state.tags.append(FakeTag(id=9, name="new"))

    resolver.invalidate("tag")
    assert (await resolver.resolve("tag", "new")).id == 9
    credentials.fingerprint = "admin"
    assert (await resolver.resolve("tag", "NEW")).id == 9
    _ = await resolver.items("correspondent")

    assert reader.loads[3:] == [("reader", "tag"), ("admin", "tag")]


@pytest.mark.anyio
async def test_items_filter_by_name_contains_insensitively(resolver: TaxonomyResolver) -> None:
    found = await resolver.items("tag", name_contains="E2E-IN")

    assert [item.name for item in found] == ["e2e-inbox", "e2e-invoices"]
    assert len(await resolver.items("tag")) == 5
