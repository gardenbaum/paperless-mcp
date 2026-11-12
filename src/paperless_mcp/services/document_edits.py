"""Document updates with merge semantics (decision-6).

Paperless replaces a document's tag and custom field lists as a whole when they are patched, so a
naive update silently drops whatever the caller did not mention. The editor reads the document
right before it writes, computes the new lists from the current ones and the requested changes,
sends only what differs, and sends nothing when the document already is as asked.

Everything predictable is checked before the first tag is created: the edit itself, the
document's change permission, names, values, linked documents and the owner. A failure after
tags were created names them.

Residual race: between the read and the PATCH (one round trip) another client may change the
same document's tags or custom fields; that change is then overwritten. Paperless has no
conditional update. Its bulk edit merges tags and custom fields on the server, but cannot set the
other fields, so using it would not make a whole update atomic either.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from paperless_mcp.domain.documents import CustomFieldValue, DocumentPatch, DocumentUpdate
from paperless_mcp.domain.errors import NotFound, PaperlessMcpError, PermissionDenied, Validation
from paperless_mcp.services.custom_values import normalise
from paperless_mcp.services.tag_hierarchy import blocked_removals, close_over_ancestors
from paperless_mcp.services.taxonomy import fold

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Sequence

    from pydantic import JsonValue

    from paperless_mcp.domain.documents import DocumentEdit, DocumentRecord, NameOrId
    from paperless_mcp.domain.ports import DocumentReader, DocumentWriter, TaxonomyWriter
    from paperless_mcp.domain.taxonomy import TaxonomyItem, TaxonomyKind
    from paperless_mcp.services.documents import DocumentService
    from paperless_mcp.services.taxonomy import TaxonomyResolver

# The taxonomy kinds whose document counts depend on who owns a document.
COUNTED: tuple[TaxonomyKind, ...] = (
    "tag",
    "correspondent",
    "document_type",
    "storage_path",
    "custom_field",
)


MAX_TITLE = 128


def _same(first: JsonValue, second: JsonValue) -> bool:
    """Equal values; ``True`` is not ``1``, and lists (document links) are compared as sets."""
    if isinstance(first, list) and isinstance(second, list):
        return sorted(map(repr, first)) == sorted(map(repr, second))
    return first == second and isinstance(first, bool) == isinstance(second, bool)


def merged_tags(current: Sequence[int], add: Iterable[int], remove: Collection[int]) -> list[int]:
    """The current tags in their order, then the added ones, without the removed ones."""
    return [tag for tag in dict.fromkeys([*current, *add]) if tag not in remove]


def merged_fields(
    current: Sequence[CustomFieldValue], sets: dict[int, JsonValue], removed: Collection[int]
) -> list[CustomFieldValue]:
    """The current custom fields in their order with ``sets`` applied and ``removed`` dropped.

    A field that is set but not yet there goes last; a field named in neither stays untouched.
    """
    kept = [
        CustomFieldValue(field=item.field, value=sets.get(item.field, item.value))
        for item in current
        if item.field not in removed
    ]
    present = {item.field for item in current}
    return kept + [
        CustomFieldValue(field=number, value=value)
        for number, value in sets.items()
        if number not in present and number not in removed
    ]


def _check(edit: DocumentEdit) -> None:
    """Reject empty and contradictory edits before anything is read."""
    if edit.tags_set is not None and (edit.tags_add or edit.tags_remove):
        message = "tags_set replaces all tags and cannot be combined with tags_add or tags_remove."
        raise Validation(message, hint="Use tags_set alone, or tags_add and tags_remove.")
    given = {
        "correspondent": edit.correspondent,
        "document_type": edit.document_type,
        "storage_path": edit.storage_path,
        "archive_serial_number": edit.archive_serial_number,
        "owner": edit.owner,
    }
    both = [name for name in edit.clear if given.get(name) is not None]
    if both:
        message = f"{', '.join(both)} is both set and cleared."
        raise Validation(message, hint="Pass a value or list it in clear, not both.")
    named = [{fold(str(tag)) for tag in group} for group in (edit.tags_add, edit.tags_remove)]
    if named[0] & named[1]:
        message = "A tag is both added and removed."
        raise Validation(message, hint="Name each tag once, in tags_add or in tags_remove.")
    if edit.title is not None and not edit.title.strip():
        message = "The title cannot be blank."
        raise Validation(message, hint="Pass a title, or leave it out to keep the current one.")
    if edit.title is not None and len(edit.title.strip()) > MAX_TITLE:
        message = f"The title is longer than {MAX_TITLE} characters."
        raise Validation(message, hint="Shorten the title.")
    if not (
        {name for name, value in given.items() if value is not None}
        or edit.clear
        or edit.title is not None
        or edit.created is not None
        or edit.tags_set is not None
        or edit.tags_add
        or edit.tags_remove
        or edit.custom_fields
        or edit.remove_custom_fields
    ):
        message = "Nothing to change: no field was given."
        raise Validation(message, hint="Pass at least one field to change, e.g. title or tags_add.")


def _numeric(value: NameOrId) -> bool:
    return isinstance(value, int) or (value.strip().isascii() and value.strip().isdigit())


@dataclass(frozen=True, slots=True)
class _Tags:
    """The tags an edit names: ids, and the names of tags to create (strings)."""

    add: list[int | str]
    remove: list[int]
    replace: list[int | str] | None
    parents: dict[int, int | None]


def _refuse_orphans(
    current: DocumentRecord, added: list[int], removed: list[int], items: dict[int, TaxonomyItem]
) -> None:
    """Refuse a removal that Paperless would extend to child tags that stay."""
    parents = {n: t.parent for n, t in items.items()}
    remaining = merged_tags(current.tags, added, removed)
    blocked = blocked_removals([t for t in removed if t in current.tags], remaining, parents)
    for tag, children in blocked.items():
        names = ", ".join(repr(items[child].name) for child in children)
        message = f"Removing {items[tag].name!r} also removes its child tags {names}."
        hint = f"List them in tags_remove too, or keep {items[tag].name!r}."
        raise Validation(message, hint=hint)


def _confirm_owner(edit: DocumentEdit, current: DocumentRecord, scalars: dict[str, object]) -> None:
    """An owner change (also to no owner) needs ``confirm``: the caller cannot undo it."""
    if "owner" in scalars and scalars["owner"] != current.owner and not edit.confirm:
        message = (
            "Changing the owner needs confirm=true: the new owner (or, with no owner, every "
            "user who may view documents) takes over the document and you may lose access to it."
        )
        raise Validation(message, hint="Pass confirm=true to change the owner.")


def _resolved(values: Iterable[int | str], created: dict[str, tuple[str, int]]) -> list[int]:
    return [created[fold(v)][1] if isinstance(v, str) else v for v in values]


def _after_creation(
    error: PaperlessMcpError, created: dict[str, tuple[str, int]], *, linked: bool
) -> PaperlessMcpError:
    """``error`` for the caller: a refused link says what it needs; created tags are named."""
    hint = error.hint
    if linked and isinstance(error, PermissionDenied):
        hint = "Linking documents also changes the linked ones: you need change permission there."
        error = PermissionDenied(
            f"{error.message} A document link needs change permission on the linked document.",
            hint=hint,
        )
    if not created:
        return error
    names = ", ".join(repr(name) for name, _ in created.values())
    return type(error)(f"{error.message} Tags created before this failed: {names}.", hint=hint)


class DocumentEditor:
    """Changes a document: names to ids, a diff against its current state, merged lists."""

    def __init__(
        self,
        reader: DocumentReader,
        writer: DocumentWriter,
        tag_writer: TaxonomyWriter,
        taxonomy: TaxonomyResolver,
        *,
        documents: DocumentService,
    ) -> None:
        """Read through ``reader``, write through ``writer``; ``documents`` renders the result."""
        self._reader: DocumentReader = reader
        self._writer: DocumentWriter = writer
        self._tag_writer: TaxonomyWriter = tag_writer
        self._taxonomy: TaxonomyResolver = taxonomy
        self._documents: DocumentService = documents

    async def update(self, document_id: int, edit: DocumentEdit) -> DocumentUpdate:
        """Apply ``edit`` to the document and return it as ``get_document`` would.

        Names are resolved and values checked first, so a bad name or value changes nothing.
        Missing tags are created only after that, and only with ``create_missing_tags``.

        Raises:
            Validation: The edit is empty or contradicts itself, a value does not fit, an owner
                change lacks ``confirm``, or a tag removal would take other tags along.
            NotFound: No such document, or a name or linked document matches nothing.
            Ambiguous: A name matches more than one object.
            PermissionDenied: The user may not change the document or create tags.
        """
        _check(edit)
        current = await self._reader.get_document(document_id)
        if not current.user_can_change:
            message = "The Paperless user may not change this document."
            raise PermissionDenied(message, hint="Ask the document's owner for change permission.")
        scalars = await self._scalars(edit)
        _confirm_owner(edit, current, scalars)
        sets, removed = await self._fields(edit, current)
        tags = await self._tags(edit, current)
        created = await self._create(tags)
        try:
            patch = DocumentPatch.model_validate(
                {
                    **self._changed(current, scalars),
                    **self._tag_change(current, tags, created),
                    **self._field_change(current, sets, removed),
                }
            )
            if not patch.model_fields_set:
                return await self._result(current, [])
            updated = await self._writer.update_document(document_id, patch)
        except PaperlessMcpError as error:
            raise _after_creation(error, created, linked=bool(sets)) from None
        for kind in self._affected(patch):
            self._taxonomy.invalidate(kind)
        return await self._result(updated, self._implicit(current, updated, tags, created))

    async def _result(self, record: DocumentRecord, implicit: list[int]) -> DocumentUpdate:
        detail = await self._documents.detail(record)
        implicit_tags = tuple(t for t in detail.tags if t.id in implicit)
        return DocumentUpdate.model_validate(
            {**cast("dict[str, object]", dict(detail)), "implicit_tags": implicit_tags}
        )

    async def _scalars(self, edit: DocumentEdit) -> dict[str, object]:
        """The plain fields the edit sets, references as ids; ``None`` clears."""
        found: dict[str, object] = {}
        for kind in ("correspondent", "document_type", "storage_path"):
            value = cast("NameOrId | None", getattr(edit, kind))
            if value is not None:
                found[kind] = (await self._taxonomy.resolve(kind, value)).id
        if edit.owner is not None:
            found["owner"] = await self._owner(edit.owner)
        plain = {
            "title": None if edit.title is None else edit.title.strip(),
            "created": edit.created,
            "archive_serial_number": edit.archive_serial_number,
        }
        found.update({name: value for name, value in plain.items() if value is not None})
        return found | dict.fromkeys(edit.clear)

    async def _owner(self, value: NameOrId) -> int:
        """The user id; an id is checked against the users unless the user may not list them."""
        try:
            return (await self._taxonomy.resolve("user", value)).id
        except PermissionDenied as error:
            if _numeric(value):
                return int(value)  # Paperless decides
            hint = "Pass the owner's user id instead of the username."
            raise PermissionDenied(error.message, hint=hint) from None

    @staticmethod
    def _changed(current: DocumentRecord, scalars: dict[str, object]) -> dict[str, object]:
        """Those of ``scalars`` that differ from the document."""
        return {name: v for name, v in scalars.items() if getattr(current, name) != v}

    async def _fields(
        self, edit: DocumentEdit, current: DocumentRecord
    ) -> tuple[dict[int, JsonValue], set[int]]:
        """The custom field values to set (checked, in stored form) and the fields to remove."""
        sets: dict[int, JsonValue] = {}
        links: set[int] = set()
        have = {item.field: item.value for item in current.custom_fields}
        for key, value in edit.custom_fields.items():
            item = await self._taxonomy.resolve("custom_field", key)
            if item.id in sets:
                message = f"Custom field {item.name!r} is given twice."
                raise Validation(message, hint="Name each field once, by name or by id.")
            sets[item.id] = normalise(item, value)
            if item.data_type == "documentlink":
                old = have.get(item.id)
                known = set(cast("list[int]", old)) if isinstance(old, list) else set[int]()
                links |= set(cast("list[int]", sets[item.id])) - known
        removed = {
            (await self._taxonomy.resolve("custom_field", f)).id for f in edit.remove_custom_fields
        }
        if sets.keys() & removed:
            message = "A custom field is both set and removed."
            raise Validation(message, hint="Set it or remove it, not both.")
        await self._visible(links)
        return sets, removed

    async def _visible(self, documents: set[int]) -> None:
        """Every linked document must be one the user sees (Paperless mirrors the link there)."""
        if not documents:
            return
        seen = await self._reader.get_document_titles(documents)
        missing = sorted(documents - seen.keys())
        if missing:
            message = f"No document {missing[0]} exists that this user may see."
            raise NotFound(message, hint="Take the id from a search_documents row.")

    @staticmethod
    def _field_change(
        current: DocumentRecord, sets: dict[int, JsonValue], removed: set[int]
    ) -> dict[str, object]:
        """The merged custom field list, if it differs from the current one."""
        have = {item.field: item.value for item in current.custom_fields}
        differing = {n: v for n, v in sets.items() if n not in have or not _same(have[n], v)}
        dropped = removed & have.keys()
        if not differing and not dropped:
            return {}
        return {"custom_fields": tuple(merged_fields(current.custom_fields, differing, dropped))}

    async def _tags(self, edit: DocumentEdit, current: DocumentRecord) -> _Tags:
        async def ids(
            values: Iterable[NameOrId], *, create: bool, adding: bool = True
        ) -> list[int | str]:
            found: list[int | str] = []
            for value in values:
                try:
                    found.append((await self._taxonomy.resolve("tag", value)).id)
                except NotFound as error:
                    if _numeric(value) or not adding:
                        raise
                    if create:
                        found.append(str(value).strip())
                        continue
                    hint = f"{error.hint} Or set create_missing_tags=true to create it."
                    raise NotFound(error.message, hint=hint) from None
            return found

        create = edit.create_missing_tags
        removed = [
            t for t in await ids(edit.tags_remove, create=False, adding=False) if isinstance(t, int)
        ]
        items = {t.id: t for t in await self._taxonomy.items("tag")}
        parents = {n: t.parent for n, t in items.items()}
        if edit.tags_set is not None:
            replace = await ids(edit.tags_set, create=create)
            return _Tags(add=[], remove=removed, replace=replace, parents=parents)
        added = await ids(edit.tags_add, create=create)
        if {t for t in added if isinstance(t, int)} & set(removed):
            message = "A tag is both added and removed."
            raise Validation(message, hint="Name each tag once, in tags_add or in tags_remove.")
        known = [t for t in added if isinstance(t, int)]
        _refuse_orphans(current, known, removed, items)
        return _Tags(add=added, remove=removed, replace=None, parents=parents)

    async def _create(self, tags: _Tags) -> dict[str, tuple[str, int]]:
        """Create the tags that do not exist yet, once per name: folded name to name and id."""
        names = [t for t in [*tags.add, *(tags.replace or [])] if isinstance(t, str)]
        created: dict[str, tuple[str, int]] = {}
        try:
            for name in names:
                if fold(name) not in created:
                    created[fold(name)] = (name, (await self._tag_writer.create_tag(name)).id)
        finally:
            if created:
                self._taxonomy.invalidate("tag")
        return created

    @staticmethod
    def _tag_change(
        current: DocumentRecord, tags: _Tags, created: dict[str, tuple[str, int]]
    ) -> dict[str, object]:
        """The new tag list, closed over the ancestors, if it differs from the current one."""
        requested = _resolved(tags.replace if tags.replace is not None else tags.add, created)
        merged = (
            list(dict.fromkeys(requested))
            if tags.replace is not None
            else merged_tags(current.tags, requested, set(tags.remove))
        )
        wanted = close_over_ancestors(merged, tags.parents)
        return {} if set(wanted) == set(current.tags) else {"tags": tuple(wanted)}

    @staticmethod
    def _implicit(
        current: DocumentRecord,
        updated: DocumentRecord,
        tags: _Tags,
        created: dict[str, tuple[str, int]],
    ) -> list[int]:
        """The tags the document gained that nobody asked for: parents of the added children."""
        asked = set(_resolved(tags.replace if tags.replace is not None else tags.add, created))
        return [t for t in updated.tags if t not in current.tags and t not in asked]

    @staticmethod
    def _affected(patch: DocumentPatch) -> list[TaxonomyKind]:
        """The kinds whose document counts the patch can change."""
        if "owner" in patch.model_fields_set:
            return list(COUNTED)
        by_field: dict[str, TaxonomyKind] = {
            "tags": "tag",
            "correspondent": "correspondent",
            "document_type": "document_type",
            "storage_path": "storage_path",
            "custom_fields": "custom_field",
        }
        return [kind for name, kind in by_field.items() if name in patch.model_fields_set]
