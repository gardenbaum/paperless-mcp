"""Saved views: found by name or id (decision-5), written with rules by label and name, run.

A view is run by translating its filter rules into the same ``DocumentQuery`` the search tool
builds and searching with it, so a view returns the rows and paging of ``search_documents``.
Writes act on the views read right before, not on the resolver's cache, and forget the resolver's
saved views afterwards, whatever happened.
"""

import difflib
from typing import TYPE_CHECKING, Final

from paperless_mcp.domain.documents import NameFilters
from paperless_mcp.domain.errors import ConfirmationRequired, NotFound, Validation, quoted
from paperless_mcp.domain.taxonomy import TaxonomyItem
from paperless_mcp.domain.views import (
    RULE_TYPES,
    FilterRule,
    RuleInfo,
    SavedViewInfo,
    SavedViewListing,
    ViewDeletion,
    ViewValues,
)
from paperless_mcp.services.filter_rules import check_sort, translate
from paperless_mcp.services.taxonomy import fold, pick

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from paperless_mcp.domain.documents import DocumentSearchResult, NameOrId
    from paperless_mcp.domain.ports import ViewReader, ViewWriter
    from paperless_mcp.domain.taxonomy import TaxonomyKind
    from paperless_mcp.domain.views import RuleInput, SavedView, ViewCreate, ViewEdit
    from paperless_mcp.services.documents import DocumentService
    from paperless_mcp.services.taxonomy import TaxonomyResolver

# The taxonomy kind whose objects the value of each rule type names.
VALUE_KINDS: Final[Mapping[int, TaxonomyKind]] = {
    **dict.fromkeys((6, 17, 22), "tag"),
    **dict.fromkeys((3, 26, 27), "correspondent"),
    **dict.fromkeys((4, 28, 29), "document_type"),
    **dict.fromkeys((25, 30, 31), "storage_path"),
    **dict.fromkeys((38, 39, 40), "custom_field"),
    **dict.fromkeys((32, 33, 35), "user"),
}
# "Correspondent is" and its siblings: no value is "not assigned", -1 is "assigned".
NULLABLE: Final = frozenset({3, 4, 25})
SUGGESTIONS: Final = 5


def plural(count: int, noun: str) -> str:
    """``count`` and ``noun`` with an s unless there is exactly one."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _described(view: SavedView) -> str:
    return f"saved view {quoted(view.name)} (id {view.id})"


def info(view: SavedView) -> SavedViewInfo:
    """The view as tools show it, its rule types with labels."""
    return SavedViewInfo(
        id=view.id,
        name=view.name,
        sort_field=view.sort_field,
        sort_reverse=view.sort_reverse,
        page_size=view.page_size,
        owner=view.owner,
        can_change=view.user_can_change,
        rules=tuple(
            RuleInfo(
                rule_type=rule.rule_type,
                name=RULE_TYPES.get(rule.rule_type, f"unknown rule type {rule.rule_type}"),
                value=rule.value,
            )
            for rule in view.filter_rules
        ),
    )


def rule_type(value: int | str) -> int:
    """The rule type number that ``value`` (a number, digits or a label) names.

    Raises:
        Validation: No rule type has that number or label; the hint lists the closest labels.
    """
    text = str(value).strip()
    if text.isascii() and text.isdigit():
        return int(text)
    by_label = {fold(label): number for number, label in RULE_TYPES.items()}
    if fold(text) in by_label:
        return by_label[fold(text)]
    close = difflib.get_close_matches(fold(text), by_label, n=SUGGESTIONS)
    names = ", ".join(repr(RULE_TYPES[by_label[key]]) for key in close)
    hint = f"Did you mean {names}? " if names else ""
    message = f"No filter rule type is called {text!r}."
    raise Validation(
        message, hint=f"{hint}Use a number from 0 to 50 or a label that create_saved_view lists."
    )


class ViewService:
    """Reads, writes and runs the saved views of the current user."""

    def __init__(
        self,
        reader: ViewReader,
        writer: ViewWriter,
        taxonomy: TaxonomyResolver,
        documents: DocumentService,
    ) -> None:
        """Read through ``reader``, write through ``writer``, search with ``documents``."""
        self._reader: ViewReader = reader
        self._writer: ViewWriter = writer
        self._taxonomy: TaxonomyResolver = taxonomy
        self._documents: DocumentService = documents

    async def list(self) -> SavedViewListing:
        """Every saved view the user may see, with its rules.

        Raises:
            PermissionDenied: The user may not view saved views.
        """
        views = await self._reader.list_saved_views()
        return SavedViewListing(count=len(views), views=tuple(info(v) for v in views))

    async def get(self, view: NameOrId) -> SavedViewInfo:
        """The view that ``view`` (a name or an id) names.

        Raises:
            NotFound: No such view; the hint lists the closest names.
            Ambiguous: The name matches several views.
            PermissionDenied: The user may not view saved views.
        """
        return info(await self._find(view))

    async def create(self, spec: ViewCreate) -> SavedViewInfo:
        """Create a view; rules are checked as ``run`` would read them before anything is sent.

        Raises:
            Validation: The name is empty or taken, or a rule or the sort field is unusable.
            NotFound: A name in a rule matches nothing.
            Ambiguous: A name in a rule matches several objects.
            PermissionDenied: The user may not add saved views.
        """
        name = self._name(spec.name)
        await self._free(name, except_id=None)
        rules = await self._rules(spec.rules)
        _ = translate(rules)
        if spec.sort_field is not None:
            check_sort(spec.sort_field)
        values = ViewValues(
            filter_rules=rules,
            sort_field=spec.sort_field,
            sort_reverse=spec.sort_reverse,
            page_size=spec.page_size,
        )
        return info(await self._write(lambda: self._writer.create_saved_view(name, values)))

    async def update(self, view: NameOrId, edit: ViewEdit) -> SavedViewInfo:
        """Change the named settings of a view; new rules replace all old ones.

        Raises:
            Validation: Nothing to change, a contradiction, or an unusable name, rule or sort.
            NotFound: No such view, or a name in a rule matches nothing.
            Ambiguous: A name matches several views or objects.
            PermissionDenied: The user may not change saved views, or this one.
        """
        if edit.sort_field is not None and edit.clear_sort:
            message = "sort_field and clear_sort contradict each other."
            raise Validation(message, hint="Pass one of them.")
        if edit.page_size is not None and edit.clear_page_size:
            message = "page_size and clear_page_size contradict each other."
            raise Validation(message, hint="Pass one of them.")
        found = await self._find(view)
        changes: dict[str, object] = {}
        if edit.name is not None:
            changes["name"] = self._name(edit.name)
            await self._free(str(changes["name"]), except_id=found.id)
        if edit.rules is not None:
            changes["filter_rules"] = await self._rules(edit.rules)
        if edit.sort_field is not None or edit.clear_sort:
            changes["sort_field"] = edit.sort_field  # None when cleared: both together are refused
        if edit.sort_reverse is not None:
            changes["sort_reverse"] = edit.sort_reverse
        if edit.page_size is not None or edit.clear_page_size:
            changes["page_size"] = edit.page_size  # None when cleared: both together are refused
        if not changes:
            message = "Nothing to change: no setting was named."
            raise Validation(message, hint="Name a new name, rules, sort_field or page_size.")
        values = ViewValues.model_validate(changes)
        # Only what the edit sets is checked: a view with a bad sort can still be renamed.
        _ = translate(values.filter_rules or ())
        if edit.sort_field is not None:
            check_sort(edit.sort_field)
        return info(await self._write(lambda: self._writer.update_saved_view(found.id, values)))

    async def delete(self, view: NameOrId, *, confirm: bool) -> ViewDeletion:
        """Delete a view for good; without ``confirm`` refuse and say what deleting it does.

        Raises:
            ConfirmationRequired: ``confirm`` is false. Nothing was changed.
            NotFound: No such view.
            Ambiguous: The name matches several views.
            PermissionDenied: The user may not delete saved views, or this one.
        """
        found = await self._find(view)
        subject = _described(found)
        if not confirm:
            rules = plural(len(found.filter_rules), "filter rule")
            effect = f"delete {subject} for good: its {rules} and its sort order are lost"
            effect = f"{effect}; no document is touched"
            raise ConfirmationRequired.for_effect(effect)
        await self._write(lambda: self._writer.delete_saved_view(found.id))
        message = f"{subject[0].upper()}{subject[1:]} was deleted; no document was touched."
        return ViewDeletion(id=found.id, name=found.name, message=message)

    async def run(self, view: NameOrId, *, page: int, page_size: int) -> DocumentSearchResult:
        """One page of the documents of a view, as ``search_documents`` would return them.

        Raises:
            Validation: A rule or the sort field of the view cannot be run; the message names
                the rule type.
            NotFound: No such view, or ``page`` is beyond the last page.
            Ambiguous: The name matches several views.
            PermissionDenied: The user may not view saved views or documents.
        """
        found = await self._find(view)
        query = translate(found.filter_rules, found.sort_field, sort_reverse=found.sort_reverse)
        return await self._documents.search(NameFilters(), query, page=page, page_size=page_size)

    async def _find(self, view: NameOrId) -> SavedView:
        views = await self._reader.list_saved_views()
        rows = [TaxonomyItem(id=v.id, name=v.name, document_count=None) for v in views]
        try:
            wanted = pick("saved_view", rows, view).id
        except NotFound as error:
            # The toolset may be on without list_taxonomy: point at its own list.
            hint = error.hint and error.hint.replace(
                "list_taxonomy(kind='saved_view')", "list_saved_views"
            )
            raise NotFound(error.message, hint=hint) from None
        return next(v for v in views if v.id == wanted)

    @staticmethod
    def _name(name: str) -> str:
        text = name.strip()
        if not text:
            message = "The view name is empty."
            raise Validation(message, hint="Give the view a name.")
        return text

    async def _free(self, name: str, *, except_id: int | None) -> None:
        """Refuse a name another visible view has (ignoring case): names find views."""
        for view in await self._reader.list_saved_views():
            if view.id != except_id and fold(view.name) == fold(name):
                message = (
                    f"The user already has a saved view named {quoted(view.name)} (id {view.id})."
                )
                raise Validation(message, hint="Choose another name, or update that view.")

    async def _rules(self, rules: Sequence[RuleInput]) -> tuple[FilterRule, ...]:
        return tuple([await self._rule(rule) for rule in rules])

    async def _rule(self, spec: RuleInput) -> FilterRule:
        """The stored rule: type by number, value as text, a name looked up as an id."""
        number = rule_type(spec.rule)
        value = spec.value
        if isinstance(value, bool):
            return FilterRule(rule_type=number, value="true" if value else "false")
        if value is None or (number in NULLABLE and str(value) == "-1"):
            return FilterRule(rule_type=number, value=None if value is None else str(value))
        text = str(value).strip()
        kind = VALUE_KINDS.get(number)
        if kind is not None and not (text.isascii() and text.isdigit()):
            text = str((await self._taxonomy.resolve(kind, text)).id)
        return FilterRule(rule_type=number, value=text)

    async def _write[T](self, write: Callable[[], Awaitable[T]]) -> T:
        try:
            return await write()
        finally:
            self._taxonomy.invalidate("saved_view")
