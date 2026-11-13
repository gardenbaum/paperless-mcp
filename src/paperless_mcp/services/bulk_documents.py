"""Bulk edits of documents: a preview first, a confirmation, one request, nothing half done.

Documents are selected by explicit ids, at most ``MAX_DOCUMENTS`` per call. The editor reads their
current state with one search, resolves names to ids, works out for every document what the
operation changes (documents that already are as asked are left alone and not sent), and then
either previews that (``dry_run``), refuses without ``confirm`` (decision-15), or sends one bulk
request for the documents that change. Paperless checks the whole batch before it changes
anything, so a refusal changes no document.

Tags follow the hierarchy rules of ``tag_hierarchy`` as ``update_document`` does: parents come
along with an added child, and removing a tag whose child tags stay is refused. Custom fields are
merged: only the named fields are set or removed, the others stay.

Residual race: between the read and the request another client may change a document; the request
then applies to its newer state (Paperless merges tags and fields on the server). The tag
hierarchy, like every name, comes from the resolver's cache (a TTL), so a tag moved elsewhere in
the last minutes can still be seen at its old place in the preview.

Change permission: a search hit says whether the user may change a document (object level; the
global permission is not visible). A batch with a document the user may not change is flagged in
the preview and refused before it is sent: Paperless would refuse it as a whole anyway. Deleting
also needs ownership, which a hit does not show; Paperless's refusal then explains.

Document links are mirrored by Paperless on the linked documents (and dropped from old targets),
without checking change permission there; the preview names those documents.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from paperless_mcp.domain.bulk import (
    MAX_DOCUMENTS,
    SAMPLE_SIZE,
    AddTags,
    DeleteDocuments,
    DocumentBulkReport,
    DocumentSample,
    FieldsChange,
    ModifyCustomFields,
    ModifyTags,
    ReferenceChange,
    RemoveTags,
    ReprocessChange,
    SetCorrespondent,
    SetDocumentType,
    SetStoragePath,
    TagsChange,
    TrashDocuments,
)
from paperless_mcp.domain.documents import DocumentQuery
from paperless_mcp.domain.errors import (
    ConfirmationRequired,
    NotFound,
    PermissionDenied,
    Validation,
    listed,
    quoted,
)
from paperless_mcp.services.custom_values import normalise
from paperless_mcp.services.document_edits import COUNTED, merged_tags, same_value
from paperless_mcp.services.documents import document_link
from paperless_mcp.services.tag_hierarchy import ancestors, blocked_removals, close_over_ancestors
from paperless_mcp.services.taxonomy_edits import documents as count_documents

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pydantic import JsonValue

    from paperless_mcp.domain.bulk import BulkOperation, DocumentChange, Reference
    from paperless_mcp.domain.documents import DocumentHit, NameOrId
    from paperless_mcp.domain.ports import BulkWriter, DocumentReader
    from paperless_mcp.domain.taxonomy import TaxonomyItem, TaxonomyKind
    from paperless_mcp.services.taxonomy import TaxonomyResolver

TRASH: Final = "(they can be restored from there)"
REPROCESS: Final = "(OCR and archiving run again in the background)"
AS_ASKED: Final = "as asked"


@dataclass(frozen=True, slots=True)
class _Operation:
    """An operation with its names resolved: the request and what it does to each document.

    ``lead`` and ``tail`` surround the documents in the operation's sentence, so the same
    sentence names "the selected documents", or a count with titles.
    """

    name: str
    lead: str
    tail: str
    change: DocumentChange
    effects: Callable[[list[DocumentHit]], dict[int, str]]
    dirty: tuple[TaxonomyKind, ...]
    # The ids of other documents the change reaches (mirrored links), given the changing ones.
    mirrored: Callable[[list[DocumentHit]], set[int]] = lambda _hits: set()
    # Whether removing tags also takes child tags the user cannot see off the documents.
    hidden_children: bool = False

    def phrase(self, docs: str) -> str:
        return f"{self.lead}{docs}{self.tail}"

    @property
    def summary(self) -> str:
        return self.phrase("the selected documents")


DRY_RUN: Final = "Dry run, nothing was changed."
TO_GO_AHEAD: Final = "To go ahead, call again without dry_run and with confirm=true."


@dataclass(frozen=True, slots=True)
class _Outcome:
    """What an operation does to a selection, for the report."""

    resolved: _Operation
    changing: list[DocumentHit]
    effects: dict[int, str]
    unchanged: int
    others: list[tuple[int, str]]
    blocked: list[DocumentHit]


def _titles(hits: list[DocumentHit]) -> str:
    """How many documents and which, e.g. 1 document ('A')."""
    return f"{count_documents(len(hits))} ({listed([hit.title for hit in hits], 3)})"


def _names(items: list[TaxonomyItem]) -> dict[int, str]:
    return {item.id: item.name for item in items}


def _unique(values: Sequence[int]) -> list[int]:
    return list(dict.fromkeys(values))


class BulkDocumentEditor:
    """Previews and applies one operation to many documents."""

    def __init__(
        self,
        reader: DocumentReader,
        writer: BulkWriter,
        taxonomy: TaxonomyResolver,
        *,
        web_url: str,
    ) -> None:
        """Read through ``reader``, write through ``writer``; ``web_url`` makes document links."""
        self._reader: DocumentReader = reader
        self._writer: BulkWriter = writer
        self._taxonomy: TaxonomyResolver = taxonomy
        self._web_url: str = web_url

    async def edit(
        self,
        document_ids: Sequence[int],
        operation: BulkOperation,
        *,
        dry_run: bool,
        confirm: bool,
    ) -> DocumentBulkReport:
        """Preview (``dry_run``) or apply ``operation`` to the documents.

        Documents that already are as asked are counted but not sent. Nothing is written for a
        preview, without ``confirm``, or when no document changes.

        Raises:
            Validation: The selection is empty or over ``MAX_DOCUMENTS``, the operation is empty
                or contradicts itself, a value does not fit, or a tag removal would take child
                tags along.
            NotFound: A document, tag, correspondent, document type, storage path or custom field
                is unknown or hidden from the user.
            Ambiguous: A name matches more than one object.
            ConfirmationRequired: ``confirm`` is false and the operation would change documents.
            PermissionDenied: The user may not change (or delete) one of the documents; Paperless
                then changed none.
        """
        selection = _unique(document_ids)
        if not 1 <= len(selection) <= MAX_DOCUMENTS:
            message = f"Select between 1 and {MAX_DOCUMENTS} documents per call."
            hint = (
                "Search with page_size 100 and edit the result page by page, or narrow the search."
            )
            raise Validation(message, hint=hint)
        resolved = await self._resolve(operation)
        hits = await self._current(selection)
        effects = resolved.effects(hits)
        changing = [hit for hit in hits if hit.id in effects]
        unchanged = len(hits) - len(changing)
        blocked = [hit for hit in changing if hit.user_can_change is False]
        if blocked and not dry_run:
            message = (
                f"You may not change {_titles(blocked)}, so Paperless would refuse the whole "
                "batch; nothing was sent."
            )
            hint = "Leave those documents out, or ask their owner for change permission."
            raise PermissionDenied(message, hint=hint)
        others = await self._others(resolved.mirrored(changing), {hit.id for hit in hits})
        if not (dry_run or not changing or confirm):
            raise ConfirmationRequired.for_effect(self._effect(resolved, changing, others))
        applied = bool(changing) and not dry_run
        if applied:
            try:
                await self._writer.bulk_edit_documents([h.id for h in changing], resolved.change)
            finally:
                for kind in resolved.dirty:
                    self._taxonomy.invalidate(kind)
        return self._report(
            _Outcome(resolved, changing, effects, unchanged, others, blocked),
            dry_run=dry_run,
            applied=applied,
        )

    async def _others(self, ids: set[int], selected: set[int]) -> list[tuple[int, str]]:
        """Titles of the documents a change reaches without selecting them ('#id' if hidden)."""
        found = sorted(ids - selected)
        if not found:
            return []
        titles = await self._reader.get_document_titles(found)
        return [(number, titles.get(number, f"#{number}")) for number in found]

    async def _current(self, selection: list[int]) -> list[DocumentHit]:
        """The selected documents in selection order; every one must exist and be visible."""
        query = DocumentQuery(ids=tuple(selection))
        page = await self._reader.search_documents(query, page=1, page_size=MAX_DOCUMENTS)
        found = {hit.id: hit for hit in page.hits}
        missing = [number for number in selection if number not in found]
        if missing:
            noun = "document" if len(missing) == 1 else "documents"
            verb = "exists" if len(missing) == 1 else "exist"
            message = f"No {noun} {', '.join(map(str, missing))} {verb} that this user may see."
            raise NotFound(message, hint="Take the ids from search_documents rows.")
        return [found[number] for number in selection]

    @staticmethod
    def _effect(
        resolved: _Operation, changing: list[DocumentHit], others: list[tuple[int, str]]
    ) -> str:
        titles = listed([hit.title for hit in changing], 3)
        effect = resolved.phrase(f"{count_documents(len(changing))} ({titles})")
        if resolved.hidden_children:
            noun = "document" if len(changing) == 1 else "documents"
            effect += f"; child tags you cannot see (other users') come off the {noun} with them"
        if others:
            verb = "that is" if len(others) == 1 else "that are"
            effect += (
                " and, because document links are mirrored, also changes "
                f"{count_documents(len(others))} {verb} not selected "
                f"({listed([title for _, title in others], 3)})"
            )
        return effect

    def _samples(self, rows: list[tuple[int, str]], change: str) -> tuple[DocumentSample, ...]:
        return tuple(
            DocumentSample(
                id=number,
                title=title,
                change=change,
                web_url=document_link(self._web_url, number),
            )
            for number, title in rows[:SAMPLE_SIZE]
        )

    def _report(self, outcome: _Outcome, *, dry_run: bool, applied: bool) -> DocumentBulkReport:
        resolved, changing, others = outcome.resolved, outcome.changing, outcome.others
        total = len(changing) + outcome.unchanged
        if outcome.blocked:
            message = (
                f"{DRY_RUN} Paperless would refuse this batch, so none would change: you may "
                f"not change {_titles(outcome.blocked)}. Leave "
                f"{'it' if len(outcome.blocked) == 1 else 'them'} out, or ask "
                f"{'its' if len(outcome.blocked) == 1 else 'their'} owner for change permission."
            )
        elif not changing:
            held = (
                "the selected document already is"
                if total == 1
                else f"all {total} selected documents already are"
            )
            message = f"Nothing to change: {held} {AS_ASKED}."
        elif dry_run:
            rest = ""
            if outcome.unchanged:
                verb = "is" if outcome.unchanged == 1 else "are"
                rest = f"; {count_documents(outcome.unchanged)} already {verb} {AS_ASKED}"
            effect = self._effect(resolved, changing, others)
            message = f"{DRY_RUN} Confirming would {effect}{rest}. {TO_GO_AHEAD}"
        else:
            message = f"Applied to {count_documents(len(changing))}: {resolved.summary}."
            if others:
                noun = "document" if len(others) == 1 else "documents"
                titles = listed([title for _, title in others], 3)
                message += f" Mirrored on {len(others)} other {noun} ({titles})."
        rows = [(hit.id, hit.title) for hit in changing]
        return DocumentBulkReport(
            operation=resolved.name,
            summary=resolved.summary,
            dry_run=dry_run,
            applied=applied,
            requested=total,
            affected_count=len(changing),
            unchanged_count=outcome.unchanged,
            sample=tuple(
                DocumentSample(
                    id=number,
                    title=title,
                    change=outcome.effects[number],
                    web_url=document_link(self._web_url, number),
                )
                for number, title in rows[:SAMPLE_SIZE]
            ),
            also_changed=self._samples(others, "link mirrored"),
            blocked=self._samples(
                [(hit.id, hit.title) for hit in outcome.blocked], "you may not change it"
            ),
            message=message,
        )

    async def _resolve(self, operation: BulkOperation) -> _Operation:
        """Names to ids and every check that needs no document."""
        if isinstance(operation, AddTags | RemoveTags | ModifyTags):
            return await self._tags(operation)
        if isinstance(operation, SetCorrespondent | SetDocumentType | SetStoragePath):
            return await self._reference(operation)
        if isinstance(operation, ModifyCustomFields):
            return await self._fields(operation)
        if isinstance(operation, DeleteDocuments):
            return _Operation(
                name=operation.op,
                lead="move ",
                tail=f" to the Paperless trash {TRASH}",
                change=TrashDocuments(),
                effects=lambda hits: dict.fromkeys((h.id for h in hits), "move to the trash"),
                dirty=COUNTED,
            )
        return _Operation(
            name=operation.op,
            lead="reprocess ",
            tail=f" {REPROCESS}",
            change=ReprocessChange(),
            effects=lambda hits: dict.fromkeys((h.id for h in hits), "reprocess"),
            dirty=(),
        )

    async def _tag_ids(self, values: Sequence[NameOrId]) -> list[int]:
        found: list[int] = []
        for value in values:
            try:
                found.append((await self._taxonomy.resolve("tag", value)).id)
            except NotFound as error:
                hint = f"{error.hint} Create it first with create_taxonomy_item(kind='tag')."
                raise NotFound(error.message, hint=hint) from None
        return _unique(found)

    async def _tags(self, operation: AddTags | RemoveTags | ModifyTags) -> _Operation:
        add = operation.add if isinstance(operation, ModifyTags) else []
        remove = operation.remove if isinstance(operation, ModifyTags) else []
        if isinstance(operation, AddTags):
            add = operation.tags
        if isinstance(operation, RemoveTags):
            remove = operation.tags
        if not (add or remove):
            message = "Nothing to change: no tag was given."
            raise Validation(message, hint="Pass tags to add or to remove.")
        added, removed = await self._tag_ids(add), await self._tag_ids(remove)
        if set(added) & set(removed):
            message = "A tag is both added and removed."
            raise Validation(message, hint="Name each tag once, to add or to remove.")
        items = await self._taxonomy.items("tag")
        names, parents = _names(items), {t.id: t.parent for t in items}
        for tag in added:
            for parent in set(ancestors(tag, parents)) & set(removed):
                message = (
                    f"Adding {quoted(names[tag])} needs its parent {quoted(names[parent])}, "
                    "which is also removed."
                )
                raise Validation(message, hint="Keep the parent, or do not add the child.")
        lead, tail = _tag_sentence([names[t] for t in added], [names[t] for t in removed])
        return _Operation(
            name=operation.op,
            lead=lead,
            tail=tail,
            change=TagsChange(add=tuple(added), remove=tuple(removed)),
            effects=lambda hits: _tag_effects(hits, added, removed, names, parents),
            dirty=("tag",),
            hidden_children=bool(removed),
        )

    async def _reference(
        self, operation: SetCorrespondent | SetDocumentType | SetStoragePath
    ) -> _Operation:
        kind: Reference
        value: NameOrId | None
        if isinstance(operation, SetCorrespondent):
            kind, value = "correspondent", operation.correspondent
        elif isinstance(operation, SetDocumentType):
            kind, value = "document_type", operation.document_type
        else:
            kind, value = "storage_path", operation.storage_path
        label = kind.replace("_", " ")
        target = None if value is None else await self._taxonomy.resolve(kind, value)
        try:
            known = _names(await self._taxonomy.items(kind))
        except PermissionDenied:
            known = {}  # clearing needs no view permission; the old name is then just its id
        wanted = None if target is None else target.id

        def column(hit: DocumentHit) -> int | None:
            return getattr(hit, kind)  # pyright: ignore[reportAny] -- the id field named by kind

        def describe(number: int | None) -> str:
            if number is None:
                return "none"
            return quoted(known[number]) if number in known else f"#{number}"

        def effects(hits: list[DocumentHit]) -> dict[int, str]:
            return {
                hit.id: f"{label} {describe(column(hit))} -> {describe(wanted)}"
                for hit in hits
                if column(hit) != wanted
            }

        if target is None:
            lead, tail = f"remove the {label} from ", ""
        else:
            lead, tail = f"set the {label} of ", f" to {quoted(target.name)}"
        return _Operation(
            name=operation.op,
            lead=lead,
            tail=tail,
            change=ReferenceChange(field=kind, value=wanted),
            effects=effects,
            dirty=(kind,),
        )

    async def _fields(self, operation: ModifyCustomFields) -> _Operation:
        if not (operation.values or operation.remove):
            message = "Nothing to change: no custom field was given."
            raise Validation(message, hint="Pass values to set or custom fields to remove.")
        sets: dict[int, JsonValue] = {}
        links: set[int] = set()
        drop_links: set[int] = set()
        names: dict[int, str] = {}
        for key, value in operation.values.items():
            item = await self._taxonomy.resolve("custom_field", key)
            if item.id in sets:
                message = f"Custom field {quoted(item.name)} is given twice."
                raise Validation(message, hint="Name each field once, by name or by id.")
            sets[item.id] = normalise(item, value)
            names[item.id] = item.name
            if item.data_type == "documentlink":
                links.add(item.id)
        removed: list[int] = []
        for key in operation.remove:
            item = await self._taxonomy.resolve("custom_field", key)
            names[item.id] = item.name
            removed.append(item.id)
            if item.data_type == "documentlink":
                drop_links.add(item.id)
        removed = _unique(removed)
        if sets.keys() & set(removed):
            message = "A custom field is both set and removed."
            raise Validation(message, hint="Set it or remove it, not both.")
        await self._linkable({n for number in links for n in _document_ids(sets[number])})
        lead = _field_sentence([names[n] for n in sets], [names[n] for n in removed])
        dropped = [n for n in removed if n in drop_links]
        return _Operation(
            name=operation.op,
            lead=lead,
            tail="",
            change=FieldsChange(values=sets, remove=tuple(removed)),
            effects=lambda hits: _field_effects(hits, sets, links, removed, names),
            dirty=("custom_field",),
            mirrored=lambda hits: _mirrored(hits, sets, links, dropped),
        )

    async def _linkable(self, documents: set[int]) -> None:
        """Every linked document must be visible and changeable: Paperless mirrors the link there.

        Raises:
            NotFound: A linked document does not exist or is hidden from the user.
            PermissionDenied: The user may not change a linked document.
        """
        if not documents:
            return
        hits = await self._current(sorted(documents))
        blocked = [hit for hit in hits if hit.user_can_change is False]
        if blocked:
            message = (
                "A link also changes the linked document, and you may not change "
                f"{_titles(blocked)}."
            )
            raise PermissionDenied(message, hint="Link only to documents you may change.")


def _tag_sentence(added: list[str], removed: list[str]) -> tuple[str, str]:
    """The words before and after the documents in a tag operation's sentence."""
    adding = f"add the tags {listed(added)}"
    removing = f"remove the tags {listed(removed)}"
    if added and removed:
        return f"{adding} and {removing} on ", ""
    return (f"{adding} to ", "") if added else (f"{removing} from ", "")


def _label(names: dict[int, str], tag: int) -> str:
    """The name of a tag, or what is known of one the user cannot see."""
    return names.get(tag) or f"#{tag} (a tag you cannot see)"


def _tag_effects(
    hits: list[DocumentHit],
    added: list[int],
    removed: list[int],
    names: dict[int, str],
    parents: dict[int, int | None],
) -> dict[int, str]:
    """Per document the tag change, or an error if a removal would take child tags along."""
    blocked: dict[int, tuple[list[int], int]] = {}
    found: dict[int, str] = {}
    for hit in hits:
        remaining = merged_tags(hit.tags, added, removed)
        refused = blocked_removals([t for t in removed if t in hit.tags], remaining, parents)
        for tag, children in refused.items():
            known, count = blocked.get(tag, ([], 0))
            blocked[tag] = (_unique([*known, *children]), count + 1)
        wanted = close_over_ancestors(remaining, parents)
        if set(wanted) != set(hit.tags):
            gained = [f"+{_label(names, t)}" for t in wanted if t not in hit.tags]
            lost = [f"-{_label(names, t)}" for t in hit.tags if t not in wanted]
            found[hit.id] = f"tags {', '.join([*gained, *lost])}"
    for tag, (children, count) in blocked.items():
        message = (
            f"Removing {quoted(names[tag])} also removes its child tags "
            f"{listed([names[child] for child in children])} (on {count_documents(count)})."
        )
        hint = f"List them in the tags to remove too, or keep {quoted(names[tag])}."
        raise Validation(message, hint=hint)
    return found


def _document_ids(value: JsonValue) -> set[int]:
    return {n for n in value if isinstance(n, int)} if isinstance(value, list) else set()


def _field_sentence(sets: list[str], removed: list[str]) -> str:
    """The words before the documents in a custom field operation's sentence."""
    setting = f"set the custom fields {listed(sets)}"
    if sets and removed:
        return f"{setting} and remove {listed(removed)} on "
    return f"{setting} on " if sets else f"remove the custom fields {listed(removed)} from "


def _field_effects(
    hits: list[DocumentHit],
    sets: dict[int, JsonValue],
    links: set[int],
    removed: list[int],
    names: dict[int, str],
) -> dict[int, str]:
    """Per document the custom fields that differ from what is asked."""
    found: dict[int, str] = {}
    for hit in hits:
        have = {v.field: v.value for v in hit.custom_fields}
        # Paperless skips a document-link field that links the document to itself.
        own = {n for n in links if hit.id in _document_ids(sets[n])}
        changing = [
            n for n in sets if n not in own and (n not in have or not same_value(have[n], sets[n]))
        ]
        dropped = [n for n in removed if n in have]
        if changing or dropped:
            texts = [names[n] for n in changing] + [f"-{names[n]}" for n in dropped]
            found[hit.id] = f"custom fields {', '.join(texts)}"
    return found


def _mirrored(
    hits: list[DocumentHit], sets: dict[int, JsonValue], links: set[int], dropped: list[int]
) -> set[int]:
    """The documents whose reverse link Paperless adds or removes for the changing ``hits``."""
    reached: set[int] = set()
    for hit in hits:
        have = {v.field: _document_ids(v.value) for v in hit.custom_fields}
        for number in links:
            wanted = _document_ids(sets[number])
            if hit.id not in wanted:  # a link to itself is skipped altogether
                # Paperless adds the reverse link on the targets; it never takes one from the
                # old targets of a changed list (3.4.2), only when the field is removed.
                reached |= wanted - have.get(number, set())
        for number in dropped:
            reached |= have.get(number, set())
    return reached
