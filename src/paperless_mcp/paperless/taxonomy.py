"""TaxonomyReader on the list endpoints of the taxonomy kinds, users and groups, and TaxonomyWriter.

Paperless filters every list by the user's permissions and answers 403 when the user lacks the
kind's global view permission. Users come with e-mail addresses and permission lists; only the
id and username leave this module.
"""

from typing import TYPE_CHECKING, Final, Literal

from pydantic import AliasChoices, BaseModel, Field

from paperless_mcp.domain.errors import PaperlessMcpError
from paperless_mcp.domain.taxonomy import (
    MatchingAlgorithm,
    SelectOption,
    TaxonomyDetail,
    TaxonomyItem,
)
from paperless_mcp.paperless.pagination import MAX_ITEMS, Page, collect

if TYPE_CHECKING:
    from paperless_mcp.domain.taxonomy import TaxonomyKind, TaxonomyValues, WritableKind
    from paperless_mcp.paperless.client import PaperlessClient

# MatchingAlgorithm.NONE: the tag is only ever set by hand.
NO_MATCHING = 0
ALGORITHMS: Final[dict[MatchingAlgorithm, int]] = {
    "none": NO_MATCHING,
    "any": 1,
    "all": 2,
    "literal": 3,
    "regex": 4,
    "fuzzy": 5,
    "auto": 6,
}
ALGORITHM_NAMES: Final[dict[int, MatchingAlgorithm]] = {v: k for k, v in ALGORITHMS.items()}
# The kinds that carry a matching rule (every writable kind but custom fields).
MATCHING_KINDS: Final = frozenset({"tag", "correspondent", "document_type", "storage_path"})
PATHS: dict[TaxonomyKind, str] = {
    "tag": "/api/tags/",
    "correspondent": "/api/correspondents/",
    "document_type": "/api/document_types/",
    "storage_path": "/api/storage_paths/",
    "custom_field": "/api/custom_fields/",
    "saved_view": "/api/saved_views/",
    "user": "/api/users/",
    "group": "/api/groups/",
}


class _Option(BaseModel):
    id: str
    label: str


class _Extra(BaseModel):
    select_options: list[_Option] | None = None


class _Row(BaseModel):
    """The fields of every kind's list row that a ``TaxonomyItem`` needs."""

    id: int
    name: str = Field(validation_alias=AliasChoices("name", "username"))
    document_count: int | None = None
    color: str | None = None
    parent: int | None = None
    data_type: str | None = None
    extra_data: _Extra | None = None
    matching_algorithm: Literal[0, 1, 2, 3, 4, 5, 6] | None = None
    match: str | None = None
    is_insensitive: bool | None = None
    is_inbox_tag: bool | None = None
    path: str | None = None


def _item(kind: TaxonomyKind, row: _Row) -> TaxonomyItem:
    tag = kind == "tag"
    options = row.extra_data.select_options if row.extra_data else None
    return TaxonomyItem(
        id=row.id,
        name=row.name,
        document_count=row.document_count,
        colour=row.color if tag else None,
        parent=row.parent if tag else None,
        data_type=row.data_type,
        select_options=(
            tuple(SelectOption(id=option.id, label=option.label) for option in options or ())
            if row.data_type == "select"
            else None
        ),
    )


class PaperlessTaxonomyReader:
    """``TaxonomyReader`` over the Paperless REST API."""

    def __init__(self, client: PaperlessClient) -> None:
        """Read through ``client``."""
        self._client: PaperlessClient = client

    async def list_taxonomy(self, kind: TaxonomyKind) -> list[TaxonomyItem]:
        """See ``TaxonomyReader.list_taxonomy``.

        Raises:
            PaperlessMcpError: More than ``MAX_ITEMS`` objects; resolving names on a part of
                them could pick the wrong one.
        """
        collected = await collect(self._client, PATHS[kind], Page[_Row])
        if collected.truncated:
            # shortcut: fails above MAX_ITEMS objects of one kind; page through on demand if
            # an instance ever needs more.
            label = kind.replace("_", " ")
            message = f"Paperless has more than {MAX_ITEMS} {label}s; this server reads up to that."
            raise PaperlessMcpError(message)
        return [_item(kind, row) for row in collected.items]


class PaperlessTaxonomyWriter:
    """``TaxonomyWriter`` over the Paperless REST API."""

    def __init__(self, client: PaperlessClient) -> None:
        """Write through ``client``."""
        self._client: PaperlessClient = client

    async def create_tag(self, name: str) -> TaxonomyItem:
        """See ``TaxonomyWriter.create_tag``.

        Matching algorithm 0 is "none": Paperless's default would train its classifier to put
        the new tag on future documents by itself. The creating user owns the tag, as in the
        web UI.
        """
        body = {"name": name, "matching_algorithm": NO_MATCHING}
        row = await self._client.call("POST", PATHS["tag"], _Row, json=body)
        # The create answer carries no count; a new tag has no documents.
        return _item("tag", row).model_copy(update={"document_count": 0})

    async def create_taxonomy_item(
        self, kind: WritableKind, name: str, values: TaxonomyValues
    ) -> TaxonomyDetail:
        """See ``TaxonomyWriter.create_taxonomy_item``."""
        body = {**_body(kind, values, create=True), "name": name}
        row = await self._client.call("POST", PATHS[kind], _Row, json=body)
        return _detail(kind, row)

    async def update_taxonomy_item(
        self, kind: WritableKind, item_id: int, values: TaxonomyValues
    ) -> TaxonomyDetail:
        """See ``TaxonomyWriter.update_taxonomy_item``.

        Two Paperless 3.4.2 rules make an update of one field need another that it does not
        name, so the object is read first and the missing field sent back unchanged:
        a select field PATCHed without its options is a 400 (whatever else it changes), and a
        regular expression is checked only if the request has both ``match`` and the algorithm.
        """
        path = f"{PATHS[kind]}{item_id}/"
        body = _body(kind, values, create=False)
        if _needs_current(kind, values):
            body = _completed(body, await self._client.get(path, _Row))
        row = await self._client.call("PATCH", path, _Row, json=body)
        return _detail(kind, row)

    async def delete_taxonomy_item(self, kind: WritableKind, item_id: int) -> None:
        """See ``TaxonomyWriter.delete_taxonomy_item``."""
        _ = await self._client.request("DELETE", f"{PATHS[kind]}{item_id}/")


def _detail(kind: WritableKind, row: _Row) -> TaxonomyDetail:
    """The row as a write answers it; an answer to a create has no count, a new object has 0."""
    item = _item(kind, row)
    algorithm = row.matching_algorithm
    rule = ALGORITHM_NAMES[algorithm] if kind in MATCHING_KINDS and algorithm is not None else None
    return TaxonomyDetail(
        id=item.id,
        name=item.name,
        document_count=row.document_count or 0,
        colour=item.colour,
        parent=item.parent,
        data_type=item.data_type,
        select_options=item.select_options,
        matching_algorithm=rule,
        match=row.match if rule else None,
        case_insensitive=row.is_insensitive if rule else None,
        is_inbox_tag=row.is_inbox_tag if kind == "tag" else None,
        path=row.path if kind == "storage_path" else None,
    )


def _needs_current(kind: WritableKind, values: TaxonomyValues) -> bool:
    """Whether the update leaves out a field that Paperless wants along with the ones it has."""
    if kind == "custom_field":
        return values.select_options is None
    sent = values.model_fields_set
    return ("match" in sent and values.matching_algorithm is None) or (
        values.matching_algorithm == "regex" and "match" not in sent
    )


def _completed(body: dict[str, object], current: _Row) -> dict[str, object]:
    """``body`` plus what ``current`` has of the fields the update left out."""
    if current.data_type == "select" and current.extra_data:
        body["extra_data"] = {
            "select_options": [
                {"id": option.id, "label": option.label}
                for option in current.extra_data.select_options or ()
            ]
        }
    if current.matching_algorithm is not None:
        if "matching_algorithm" not in body:
            body["matching_algorithm"] = current.matching_algorithm
        if "match" not in body and body["matching_algorithm"] == ALGORITHMS["regex"]:
            body["match"] = current.match or ""
    return body


def _options(values: TaxonomyValues) -> list[dict[str, str]]:
    return [
        {"id": option.id, "label": option.label} if option.id else {"label": option.label}
        for option in values.select_options or ()
    ]


def _body(kind: WritableKind, values: TaxonomyValues, *, create: bool) -> dict[str, object]:
    """The request body for the settings ``values`` sets, under Paperless's names."""
    sent = values.model_fields_set
    body: dict[str, object] = {}
    for name, wire in (
        ("name", "name"),
        ("match", "match"),
        ("case_insensitive", "is_insensitive"),
        ("colour", "color"),
        ("parent", "parent"),
        ("is_inbox_tag", "is_inbox_tag"),
        ("path", "path"),
        ("data_type", "data_type"),
    ):
        if name in sent:
            body[wire] = getattr(values, name)
    if values.matching_algorithm is not None:
        body["matching_algorithm"] = ALGORITHMS[values.matching_algorithm]
    elif create and kind in MATCHING_KINDS:
        body["matching_algorithm"] = NO_MATCHING
    if values.select_options is not None:
        body["extra_data"] = {"select_options": _options(values)}
    return body
