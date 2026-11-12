"""Document updates with merge semantics (decision-6).

Paperless replaces a document's tag and custom field lists as a whole when they are patched, so a
naive update silently drops whatever the caller did not mention. The editor reads the document
right before it writes, computes the new lists from the current ones and the requested changes,
sends only what differs, and sends nothing when the document already is as asked.

Residual race: between the read and the PATCH (one round trip) another client may change the
same document's tags or custom fields; that change is then overwritten. Paperless has no
conditional update. Its bulk edit merges custom fields on the server, but cannot set the other
fields, so using it would not make a whole update atomic either.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from paperless_mcp.domain.documents import CustomFieldValue, DocumentPatch
from paperless_mcp.domain.errors import NotFound, Validation
from paperless_mcp.services.custom_values import normalise
from paperless_mcp.services.documents import owner_id
from paperless_mcp.services.taxonomy import fold

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Sequence

    from pydantic import JsonValue

    from paperless_mcp.domain.documents import (
        DocumentDetail,
        DocumentEdit,
        DocumentRecord,
        NameOrId,
    )
    from paperless_mcp.domain.ports import DocumentReader, DocumentWriter, TaxonomyWriter
    from paperless_mcp.domain.taxonomy import TaxonomyKind
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


def _same(first: JsonValue, second: JsonValue) -> bool:
    """Equal values; ``True`` is not ``1``."""
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


@dataclass(frozen=True, slots=True)
class _Tags:
    """The tags an edit names: ids, and the names of tags to create (strings)."""

    add: list[int | str]
    remove: list[int]
    replace: list[int | str] | None


def _numeric(value: NameOrId) -> bool:
    return isinstance(value, int) or (value.strip().isascii() and value.strip().isdigit())


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

    async def update(self, document_id: int, edit: DocumentEdit) -> DocumentDetail:
        """Apply ``edit`` to the document and return it as ``get_document`` would.

        Names are resolved and values checked first, so a bad name or value changes nothing.
        Missing tags are created only after that, and only with ``create_missing_tags``.

        Raises:
            Validation: The edit is empty or contradicts itself, or a value does not fit.
            NotFound: No such document, or a name matches nothing.
            Ambiguous: A name matches more than one object.
            PermissionDenied: The user may not change the document or create tags.
        """
        _check(edit)
        current = await self._reader.get_document(document_id)
        scalars = await self._scalars(edit)
        sets, removed = await self._fields(edit)
        tags = await self._tags(edit)
        created = await self._create(tags)
        patch = DocumentPatch.model_validate(
            {
                **self._changed(current, scalars),
                **self._tag_change(current, tags, created),
                **self._field_change(current, sets, removed),
            }
        )
        if not patch.model_fields_set:
            return await self._documents.detail(current)
        updated = await self._writer.update_document(document_id, patch)
        for kind in self._affected(patch):
            self._taxonomy.invalidate(kind)
        return await self._documents.detail(updated)

    async def _scalars(self, edit: DocumentEdit) -> dict[str, object]:
        """The plain fields the edit sets, references as ids; ``None`` clears."""
        found: dict[str, object] = {}
        for kind in ("correspondent", "document_type", "storage_path"):
            value = cast("NameOrId | None", getattr(edit, kind))
            if value is not None:
                found[kind] = (await self._taxonomy.resolve(kind, value)).id
        if edit.owner is not None:
            found["owner"] = await owner_id(self._taxonomy, edit.owner)
        plain = {
            "title": edit.title,
            "created": edit.created,
            "archive_serial_number": edit.archive_serial_number,
        }
        found.update({name: value for name, value in plain.items() if value is not None})
        return found | dict.fromkeys(edit.clear)

    @staticmethod
    def _changed(current: DocumentRecord, scalars: dict[str, object]) -> dict[str, object]:
        """Those of ``scalars`` that differ from the document."""
        return {name: v for name, v in scalars.items() if getattr(current, name) != v}

    async def _fields(self, edit: DocumentEdit) -> tuple[dict[int, JsonValue], set[int]]:
        """The custom field values to set (checked, in stored form) and the fields to remove."""
        sets: dict[int, JsonValue] = {}
        for key, value in edit.custom_fields.items():
            item = await self._taxonomy.resolve("custom_field", key)
            if item.id in sets:
                message = f"Custom field {item.name!r} is given twice."
                raise Validation(message, hint="Name each field once, by name or by id.")
            sets[item.id] = normalise(item, value)
        removed = {
            (await self._taxonomy.resolve("custom_field", f)).id for f in edit.remove_custom_fields
        }
        if sets.keys() & removed:
            message = "A custom field is both set and removed."
            raise Validation(message, hint="Set it or remove it, not both.")
        return sets, removed

    def _field_change(
        self, current: DocumentRecord, sets: dict[int, JsonValue], removed: set[int]
    ) -> dict[str, object]:
        """The merged custom field list, if it differs from the current one."""
        have = {item.field: item.value for item in current.custom_fields}
        differing = {n: v for n, v in sets.items() if n not in have or not _same(have[n], v)}
        dropped = removed & have.keys()
        if not differing and not dropped:
            return {}
        return {"custom_fields": tuple(merged_fields(current.custom_fields, differing, dropped))}

    async def _tags(self, edit: DocumentEdit) -> _Tags:
        async def ids(values: Iterable[NameOrId], *, create: bool) -> list[int | str]:
            found: list[int | str] = []
            for value in values:
                try:
                    found.append((await self._taxonomy.resolve("tag", value)).id)
                except NotFound as error:
                    if _numeric(value):
                        raise
                    if create:
                        found.append(str(value).strip())
                        continue
                    hint = f"{error.hint} Or set create_missing_tags=true to create it."
                    raise NotFound(error.message, hint=hint) from None
            return found

        create = edit.create_missing_tags
        removed = [t for t in await ids(edit.tags_remove, create=False) if isinstance(t, int)]
        if edit.tags_set is not None:
            return _Tags(add=[], remove=removed, replace=await ids(edit.tags_set, create=create))
        added = await ids(edit.tags_add, create=create)
        if {t for t in added if isinstance(t, int)} & set(removed):
            message = "A tag is both added and removed."
            raise Validation(message, hint="Name each tag once, in tags_add or in tags_remove.")
        return _Tags(add=added, remove=removed, replace=None)

    async def _create(self, tags: _Tags) -> dict[str, int]:
        """Create the tags that do not exist yet, once per name; the ids by folded name."""
        names = [t for t in [*tags.add, *(tags.replace or [])] if isinstance(t, str)]
        created: dict[str, int] = {}
        try:
            for name in names:
                if fold(name) not in created:
                    created[fold(name)] = (await self._tag_writer.create_tag(name)).id
        finally:
            if created:
                self._taxonomy.invalidate("tag")
        return created

    @staticmethod
    def _tag_change(
        current: DocumentRecord, tags: _Tags, created: dict[str, int]
    ) -> dict[str, object]:
        """The new tag list, if it differs from the current one."""

        def resolved(values: Iterable[int | str]) -> list[int]:
            return [created[fold(v)] if isinstance(v, str) else v for v in values]

        if tags.replace is not None:
            wanted = list(dict.fromkeys(resolved(tags.replace)))
        else:
            wanted = merged_tags(current.tags, resolved(tags.add), set(tags.remove))
        return {} if set(wanted) == set(current.tags) else {"tags": tuple(wanted)}

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
