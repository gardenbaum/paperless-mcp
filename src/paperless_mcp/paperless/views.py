"""ViewReader and ViewWriter on ``/api/saved_views/``.

Paperless filters the list by the user's permissions and answers 403 to a user without the global
``view_savedview`` permission. The display settings of the web UI (icon, display mode and fields)
never leave this module.
"""

from typing import TYPE_CHECKING

from pydantic import BaseModel

from paperless_mcp.domain.errors import NotFound, PaperlessMcpError
from paperless_mcp.domain.views import FilterRule, SavedView
from paperless_mcp.paperless.pagination import MAX_ITEMS, Page, collect

if TYPE_CHECKING:
    from paperless_mcp.domain.views import ViewValues
    from paperless_mcp.paperless.client import PaperlessClient

PATH = "/api/saved_views/"


class _Rule(BaseModel):
    rule_type: int
    value: str | None = None


class _Row(BaseModel):
    id: int
    name: str
    sort_field: str | None
    sort_reverse: bool
    page_size: int | None
    owner: int | None
    user_can_change: bool
    filter_rules: list[_Rule]


def _view(row: _Row) -> SavedView:
    return SavedView(
        id=row.id,
        name=row.name,
        sort_field=row.sort_field,
        sort_reverse=row.sort_reverse,
        page_size=row.page_size,
        owner=row.owner,
        user_can_change=row.user_can_change,
        filter_rules=tuple(
            FilterRule(rule_type=r.rule_type, value=r.value) for r in row.filter_rules
        ),
    )


def unseen_view(view_id: int) -> NotFound:
    """The error for a view that does not exist or that the user may not see."""
    message = f"No saved view {view_id} exists that this user may see."
    return NotFound(message, hint="Call list_saved_views to see the views you can use.")


def _body(values: ViewValues) -> dict[str, object]:
    """The request body for the settings ``values`` sets, under Paperless's names."""
    sent = values.model_fields_set
    body: dict[str, object] = {}
    if values.name is not None:
        body["name"] = values.name
    if values.filter_rules is not None:
        body["filter_rules"] = [
            {"rule_type": rule.rule_type, "value": rule.value} for rule in values.filter_rules
        ]
    for name in ("sort_field", "sort_reverse", "page_size"):
        if name in sent:
            body[name] = getattr(values, name)
    return body


class PaperlessViewReader:
    """``ViewReader`` over the Paperless REST API."""

    def __init__(self, client: PaperlessClient) -> None:
        """Read through ``client``."""
        self._client: PaperlessClient = client

    async def list_saved_views(self) -> tuple[SavedView, ...]:
        """See ``ViewReader.list_saved_views``.

        Raises:
            PaperlessMcpError: More than ``MAX_ITEMS`` views; resolving names on a part of them
                could pick the wrong one.
        """
        collected = await collect(self._client, PATH, Page[_Row])
        if collected.truncated:
            message = (
                f"Paperless has more than {MAX_ITEMS} saved views; this server reads up to that."
            )
            raise PaperlessMcpError(message)
        return tuple(_view(row) for row in collected.items)

    async def get_saved_view(self, view_id: int) -> SavedView:
        """See ``ViewReader.get_saved_view``."""
        try:
            return _view(await self._client.get(f"{PATH}{view_id}/", _Row))
        except NotFound:
            raise unseen_view(view_id) from None


class PaperlessViewWriter:
    """``ViewWriter`` over the Paperless REST API."""

    def __init__(self, client: PaperlessClient) -> None:
        """Write through ``client``."""
        self._client: PaperlessClient = client

    async def create_saved_view(self, name: str, values: ViewValues) -> SavedView:
        """See ``ViewWriter.create_saved_view``: Paperless requires the rule list, even empty."""
        body: dict[str, object] = {"filter_rules": [], **_body(values), "name": name}
        return _view(await self._client.call("POST", PATH, _Row, json=body))

    async def update_saved_view(self, view_id: int, values: ViewValues) -> SavedView:
        """See ``ViewWriter.update_saved_view``."""
        try:
            row = await self._client.call("PATCH", f"{PATH}{view_id}/", _Row, json=_body(values))
        except NotFound:
            raise unseen_view(view_id) from None
        return _view(row)

    async def delete_saved_view(self, view_id: int) -> None:
        """See ``ViewWriter.delete_saved_view``."""
        try:
            _ = await self._client.request("DELETE", f"{PATH}{view_id}/")
        except NotFound:
            raise unseen_view(view_id) from None
