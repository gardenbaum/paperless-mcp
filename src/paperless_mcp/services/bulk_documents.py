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
then applies to its newer state (Paperless merges tags and fields on the server).
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
from paperless_mcp.services.tag_hierarchy import blocked_removals, close_over_ancestors
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

    def phrase(self, docs: str) -> str:
        return f"{self.lead}{docs}{self.tail}"

    @property
    def summary(self) -> str:
        return self.phrase("the selected documents")


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
        if not (dry_run or not changing or confirm):
            raise ConfirmationRequired.for_effect(self._effect(resolved, changing))
        applied = bool(changing) and not dry_run
        if applied:
            try:
                await self._writer.bulk_edit_documents([h.id for h in changing], resolved.change)
            finally:
                for kind in resolved.dirty:
                    self._taxonomy.invalidate(kind)
        return self._report(
            resolved, changing, effects, unchanged, dry_run=dry_run, applied=applied
        )

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
    def _effect(resolved: _Operation, changing: list[DocumentHit]) -> str:
        titles = listed([hit.title for hit in changing], 3)
        return resolved.phrase(f"{count_documents(len(changing))} ({titles})")

    def _report(  # noqa: PLR0913 -- the pieces of one report
        self,
        resolved: _Operation,
        changing: list[DocumentHit],
        effects: dict[int, str],
        unchanged: int,
        *,
        dry_run: bool,
        applied: bool,
    ) -> DocumentBulkReport:
        total = len(changing) + unchanged
        if not changing:
            held = (
                "the selected document already is"
                if total == 1
                else f"all {total} selected documents already are"
            )
            message = f"Nothing to change: {held} {AS_ASKED}."
        elif dry_run:
            rest = ""
            if unchanged:
                verb = "is" if unchanged == 1 else "are"
                rest = f"; {count_documents(unchanged)} already {verb} {AS_ASKED}"
            message = (
                f"Dry run, nothing was changed. Confirming would "
                f"{self._effect(resolved, changing)}{rest}. "
                "To go ahead, call again without dry_run and with confirm=true."
            )
        else:
            message = f"Applied to {count_documents(len(changing))}: {resolved.summary}."
        sample = tuple(
            DocumentSample(
                id=hit.id,
                title=hit.title,
                change=effects[hit.id],
                web_url=document_link(self._web_url, hit.id),
            )
            for hit in changing[:SAMPLE_SIZE]
        )
        return DocumentBulkReport(
            operation=resolved.name,
            summary=resolved.summary,
            dry_run=dry_run,
            applied=applied,
            requested=total,
            affected_count=len(changing),
            unchanged_count=unchanged,
            sample=sample,
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
        lead, tail = _tag_sentence([names[t] for t in added], [names[t] for t in removed])
        return _Operation(
            name=operation.op,
            lead=lead,
            tail=tail,
            change=TagsChange(add=tuple(added), remove=tuple(removed)),
            effects=lambda hits: _tag_effects(hits, added, removed, names, parents),
            dirty=("tag",),
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
        removed = _unique(removed)
        if sets.keys() & set(removed):
            message = "A custom field is both set and removed."
            raise Validation(message, hint="Set it or remove it, not both.")
        await self._visible({n for number in links for n in _document_ids(sets[number])})
        lead = _field_sentence([names[n] for n in sets], [names[n] for n in removed])
        return _Operation(
            name=operation.op,
            lead=lead,
            tail="",
            change=FieldsChange(values=sets, remove=tuple(removed)),
            effects=lambda hits: _field_effects(hits, sets, links, removed, names),
            dirty=("custom_field",),
        )

    async def _visible(self, documents: set[int]) -> None:
        """Every linked document must be one the user sees (Paperless mirrors the link there)."""
        if not documents:
            return
        seen = await self._reader.get_document_titles(documents)
        missing = sorted(documents - seen.keys())
        if missing:
            message = f"No document {missing[0]} exists that this user may see."
            raise NotFound(message, hint="Take the id from a search_documents row.")


def _tag_sentence(added: list[str], removed: list[str]) -> tuple[str, str]:
    """The words before and after the documents in a tag operation's sentence."""
    adding = f"add the tags {listed(added)}"
    removing = f"remove the tags {listed(removed)}"
    if added and removed:
        return f"{adding} and {removing} on ", ""
    return (f"{adding} to ", "") if added else (f"{removing} from ", "")


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
            gained = [f"+{names[t]}" for t in wanted if t not in hit.tags]
            lost = [f"-{names[t]}" for t in hit.tags if t not in wanted]
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
