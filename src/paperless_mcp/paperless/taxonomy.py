"""TaxonomyReader on the list endpoints of the taxonomy kinds, users and groups.

Paperless filters every list by the user's permissions and answers 403 when the user lacks the
kind's global view permission. Users come with e-mail addresses and permission lists; only the
id and username leave this module.
"""

from typing import TYPE_CHECKING

from pydantic import AliasChoices, BaseModel, Field

from paperless_mcp.domain.errors import PaperlessMcpError
from paperless_mcp.domain.taxonomy import TaxonomyItem
from paperless_mcp.paperless.pagination import MAX_ITEMS, Page, collect

if TYPE_CHECKING:
    from paperless_mcp.domain.taxonomy import TaxonomyKind
    from paperless_mcp.paperless.client import PaperlessClient

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
            tuple(option.label for option in options or ()) if row.data_type == "select" else None
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
