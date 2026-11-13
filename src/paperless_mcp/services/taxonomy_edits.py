"""Creating, changing and deleting taxonomy objects, with the checks Paperless leaves to us.

Paperless accepts more than is good for callers (decision-6): it keeps a select option twice,
regenerates every option id when a change leaves the ids out, lets a custom field change its
data type under the documents that hold values, and deletes a tag with all its child tags. The
editor therefore checks which settings a kind has, keeps option ids stable by sending them back,
never offers a data type change, and makes the destructive steps (deleting an object, removing
select options) ask for ``confirm``.

Writes act on the current state, read right before, not on the resolver's cache, and drop the
resolver's entries of the kind afterwards, also when the write fails (a timeout does not say
whether it happened).
"""

import re
from typing import TYPE_CHECKING, Final

from paperless_mcp.domain.documents import DocumentQuery
from paperless_mcp.domain.errors import Ambiguous, ConfirmationRequired, Validation, listed, quoted
from paperless_mcp.domain.taxonomy import (
    DeletedTag,
    OptionInput,
    SelectOption,
    TaxonomyDeletion,
    TaxonomyDetail,
    TaxonomyValues,
)
from paperless_mcp.services.tag_hierarchy import descendants
from paperless_mcp.services.taxonomy import fold, pick

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from pydantic import JsonValue

    from paperless_mcp.domain.documents import NameOrId
    from paperless_mcp.domain.ports import DocumentReader, TaxonomyReader, TaxonomyWriter
    from paperless_mcp.domain.taxonomy import TaxonomyItem, WritableKind
    from paperless_mcp.domain.taxonomy_edits import TaxonomyCreate, TaxonomyEdit, TaxonomyFields
    from paperless_mcp.services.taxonomy import TaxonomyResolver

MAX_NAME = 128
MATCHING: Final = ("tag", "correspondent", "document_type", "storage_path")
# Which kinds take a setting; a setting not listed here belongs to every kind.
APPLIES_TO: Final[dict[str, tuple[WritableKind, ...]]] = {
    "matching_algorithm": MATCHING,
    "match": MATCHING,
    "case_insensitive": MATCHING,
    "colour": ("tag",),
    "parent": ("tag",),
    "is_inbox_tag": ("tag",),
    "path": ("storage_path",),
    "data_type": ("custom_field",),
    "select_options": ("custom_field",),
}
LABELS: Final[dict[WritableKind, str]] = {
    "tag": "tag",
    "correspondent": "correspondent",
    "document_type": "document type",
    "storage_path": "storage path",
    "custom_field": "custom field",
}
COLOUR = re.compile(r"#[0-9a-fA-F]{6}")
PATH_HINT = (
    "A storage path is a template, e.g. '{{ created_year }}/{{ correspondent }}/{{ title }}'. "
    "Variables include asn, correspondent, document_type, tag_list, title, created, "
    "created_year, created_month, created_day, added, added_year, owner_username, "
    "original_name, doc_pk and custom_fields."
)
# Paperless prefixes a rejection with the name of the field it concerns.
PATH_FIELD = "path: "
HIDDEN_CHILDREN = "child tags you cannot see (other users') are deleted with it as well"
# What deleting an object does to the documents that use it, by kind (a clause of the refusal).
EFFECTS: Final[dict[WritableKind, str]] = {
    "tag": "it is removed from its documents ({docs})",
    "correspondent": "the documents that use it ({docs}) lose their correspondent",
    "document_type": "the documents that use it ({docs}) lose their document type",
    "storage_path": "the documents that use it ({docs}) lose their storage path",
    "custom_field": "the documents that have the field ({docs}) lose the field and its values",
}


def documents(count: int) -> str:
    """The count with its noun: "1 document", "2 documents"."""
    return f"{count} document" + ("" if count == 1 else "s")


def _name(name: str) -> str:
    name = name.strip()
    if not name or len(name) > MAX_NAME:
        message = f"The name must have 1 to {MAX_NAME} characters."
        raise Validation(message, hint="Give a short, non-blank name.")
    return name


def _applies(kind: WritableKind, fields: TaxonomyFields) -> None:
    """Refuse the settings that are not settings of ``kind``."""
    for name, kinds in APPLIES_TO.items():
        if kind not in kinds and getattr(fields, name, None) not in (None, ()):
            message = f"{name} is not a setting of a {LABELS[kind]}."
            listing = " and ".join(LABELS[k] for k in kinds)
            raise Validation(message, hint=f"{name} belongs to {listing}; leave it out.")


def _colour(colour: str) -> str:
    if not COLOUR.fullmatch(colour):
        message = f"A colour is '#rrggbb' (six hexadecimal digits), not {colour!r}."
        raise Validation(message, hint="For example '#a6cee3'.")
    return colour


def _label(label: str) -> str:
    label = label.strip()
    if not label:
        message = "An option label cannot be blank."
        raise Validation(message, hint="Give every option a label.")
    return label


def _unique(labels: Sequence[tuple[str, bool]], field: str) -> None:
    """Refuse a label twice (ignoring case); each label comes with whether it is new."""
    seen: dict[str, bool] = {}
    for label, new in labels:
        key = fold(label)
        if key in seen:
            if new and seen[key]:
                message = f"The option {label!r} is given twice for {field!r}."
            else:
                message = f"{field!r} already has an option {label!r} (ignoring case)."
            hint = "Choose another label, or rename the other option (by its id) first."
            raise Validation(message, hint=hint)
        seen[key] = new


def _find(options: Sequence[SelectOption], ref: str, field: str) -> SelectOption:
    """The option ``ref`` names: its label, its id, or its label ignoring case and accents."""
    for found in (
        [o for o in options if o.label == ref],
        [o for o in options if o.id == ref],
        [o for o in options if fold(o.label) == fold(ref)],
    ):
        if len(found) == 1:
            return found[0]
        if found:
            message = f"Several options of {field!r} match {ref!r}."
            hint = "Pass the id of the option; list_taxonomy(kind='custom_field') shows the ids."
            raise Ambiguous(message, hint=hint)
    message = f"{field!r} has no option {ref!r}."
    listing = ", ".join(repr(o.label) for o in options)
    raise Validation(message, hint=f"Options: {listing}.")


class TaxonomyEditor:
    """Creates, updates and deletes tags, correspondents, document types, paths and fields."""

    def __init__(
        self,
        reader: TaxonomyReader,
        writer: TaxonomyWriter,
        resolver: TaxonomyResolver,
        documents: DocumentReader,
    ) -> None:
        """Read the current state through ``reader``, write through ``writer``.

        ``resolver`` is the cache that every write invalidates; ``documents`` counts the
        documents that hold an option about to be removed.
        """
        self._documents: DocumentReader = documents
        self._reader: TaxonomyReader = reader
        self._writer: TaxonomyWriter = writer
        self._resolver: TaxonomyResolver = resolver

    async def create(self, kind: WritableKind, create: TaxonomyCreate) -> TaxonomyDetail:
        """A new object, owned by the user.

        Raises:
            Validation: A setting does not belong to ``kind``, a required one is missing or does
                not fit, or Paperless refused a value (a duplicate name, a bad template).
            NotFound: The parent tag matches nothing.
            Ambiguous: The parent tag name matches several tags.
            PermissionDenied: The user may not add objects of ``kind``.
        """
        _applies(kind, create)
        name = _name(create.name)
        if create.match is not None and create.matching_algorithm is None:
            message = "A match needs a matching_algorithm to say how it is used."
            raise Validation(message, hint="Use any, all, literal, regex or fuzzy.")
        given = await self._settings(create)
        given.update(self._required(kind, create))
        values = TaxonomyValues.model_validate(given)
        return await self._write(
            kind, lambda: self._writer.create_taxonomy_item(kind, name, values)
        )

    @staticmethod
    def _required(kind: WritableKind, create: TaxonomyCreate) -> dict[str, object]:
        """What a kind needs to be created: a path template, a data type, select options."""
        if kind == "storage_path" and (create.path is None or not create.path.strip()):
            message = "A storage path needs a path template."
            raise Validation(message, hint=PATH_HINT)
        if kind != "custom_field":
            return {}
        if create.data_type is None:
            message = "A custom field needs a data_type."
            hint = "One of string, longtext, url, date, boolean, integer, float, monetary, "
            raise Validation(message, hint=hint + "documentlink, select.")
        if create.data_type != "select":
            if create.select_options:
                message = "select_options are only for select fields."
                raise Validation(message, hint="Leave them out or use data_type select.")
            return {"data_type": create.data_type}
        labels = [_label(label) for label in create.select_options]
        if not labels:
            message = "A select field needs at least one option."
            raise Validation(message, hint="Pass select_options, e.g. ['yes', 'no'].")
        _unique([(label, True) for label in labels], "the new field")
        return {
            "data_type": "select",
            "select_options": tuple(OptionInput(label=label) for label in labels),
        }

    async def _settings(self, fields: TaxonomyFields) -> dict[str, object]:
        """The settings of ``fields`` that are given, checked, with the parent as an id."""
        given: dict[str, object] = {}
        for name in ("matching_algorithm", "match", "case_insensitive", "is_inbox_tag", "path"):
            if getattr(fields, name) is not None:
                given[name] = getattr(fields, name)
        if fields.colour is not None:
            given["colour"] = _colour(fields.colour)
        if fields.parent is not None:
            tags = await self._reader.list_taxonomy("tag")
            given["parent"] = pick("tag", tags, fields.parent).id
        return given

    async def update(
        self, kind: WritableKind, item: NameOrId, edit: TaxonomyEdit
    ) -> TaxonomyDetail:
        """Change the settings ``edit`` names on the object ``item`` names; the rest stays.

        Raises:
            Validation: Nothing to change, a setting does not belong to ``kind``, an option
                change makes no sense or lacks ``confirm``, or Paperless refused a value (a
                parent that would make a cycle, a duplicate name, a bad template).
            NotFound: No such object, or the parent tag matches nothing.
            Ambiguous: A name matches several objects or options.
            PermissionDenied: The user may not change objects of ``kind``, or this one.
        """
        _applies(kind, edit)
        options_asked = bool(edit.options_add or edit.options_rename or edit.options_remove)
        if options_asked and kind != "custom_field":
            message = f"A {LABELS[kind]} has no select options: only select custom fields do."
            raise Validation(message, hint="Options belong to custom fields of data type select.")
        if edit.clear_parent and kind != "tag":
            message = f"A {LABELS[kind]} has no parent: only tags do."
            raise Validation(message, hint="Leave clear_parent out.")
        given = await self._settings(edit)
        if edit.name is not None:
            given["name"] = _name(edit.name)
        if edit.clear_parent:
            if "parent" in given:
                message = "Give parent or clear_parent, not both."
                raise Validation(message, hint="clear_parent=true makes the tag a top-level tag.")
            given["parent"] = None
        if not given and not options_asked:
            message = "Nothing to change: name at least one setting."
            raise Validation(message, hint="For example name, colour or match.")
        current = pick(kind, await self._reader.list_taxonomy(kind), item)
        if options_asked:
            given["select_options"] = await self._options(current, edit)
        patch = TaxonomyValues.model_validate(given)
        return await self._write(
            kind, lambda: self._writer.update_taxonomy_item(kind, current.id, patch)
        )

    async def _options(self, field: TaxonomyItem, edit: TaxonomyEdit) -> tuple[OptionInput, ...]:
        """The whole new option list; options that stay keep their ids."""
        if field.data_type != "select":
            message = f"{field.name!r} has no select options: only select custom fields do."
            raise Validation(message, hint="Options belong to custom fields of data type select.")
        current = field.select_options or ()
        renamed = {
            _find(current, ref, field.name).id: _label(label)
            for ref, label in edit.options_rename.items()
        }
        removed = {_find(current, ref, field.name).id for ref in edit.options_remove}
        if renamed.keys() & removed:
            message = "An option cannot be both renamed and removed."
            raise Validation(message, hint="Rename it or remove it, not both.")
        added = [_label(label) for label in edit.options_add]
        kept = [
            (option.id, renamed.get(option.id, option.label))
            for option in current
            if option.id not in removed
        ]
        _unique([*((label, False) for _, label in kept), *((a, True) for a in added)], field.name)
        if not kept and not added:
            message = "A select field needs at least one option."
            raise Validation(message, hint="Keep at least one option or add a new one.")
        if removed and not edit.confirm:
            holding = await self._holding(field, removed)
            gone = [o.label for o in current if o.id in removed]
            effect = (
                f"remove the option(s) {listed(gone)} of {quoted(field.name)}: "
                f"{documents(holding)} you can see that hold a removed option lose their value for "
                "this field, which Paperless clears shortly after"
            )
            raise ConfirmationRequired.for_effect(effect)
        return (
            *(OptionInput(id=number, label=label) for number, label in kept),
            *(OptionInput(label=label) for label in added),
        )

    async def _holding(self, field: TaxonomyItem, removed: set[str]) -> int:
        """How many documents the caller can see that hold one of the ``removed`` option ids."""
        ids: list[JsonValue] = [*sorted(removed)]
        query = DocumentQuery(custom_field_query=[field.name, "in", ids])
        return (await self._documents.search_documents(query, page=1, page_size=1)).count

    async def delete(
        self, kind: WritableKind, item: NameOrId, *, confirm: bool
    ) -> TaxonomyDeletion:
        """Delete the object; without ``confirm`` refuse and say what deleting it would do.

        Raises:
            ConfirmationRequired: ``confirm`` is false. Nothing was changed.
            NotFound: No such object.
            Ambiguous: The name matches several objects.
            PermissionDenied: The user may not delete objects of ``kind``, or this one.
        """
        rows = await self._reader.list_taxonomy(kind)
        found = pick(kind, rows, item)
        count = found.document_count or 0
        parents = {row.id: row.parent for row in rows}
        gone: set[int] = descendants(found.id, parents) if kind == "tag" else set()
        below = tuple(
            DeletedTag(name=row.name, document_count=row.document_count or 0)
            for row in sorted(rows, key=lambda row: row.name)
            if row.id in gone
        )
        effect = EFFECTS[kind].format(docs=documents(count))
        if kind == "tag":
            if below:
                children = listed([f"{t.name} ({documents(t.document_count)})" for t in below])
                effect += f"; its child tags {children} are deleted with it"
            effect += f"; {HIDDEN_CHILDREN}"
        subject = f"{LABELS[kind]} {quoted(found.name)} (id {found.id})"
        if not confirm:
            msg = f"delete {subject} for good: {effect}"
            raise ConfirmationRequired.for_effect(msg)
        await self._write(kind, lambda: self._writer.delete_taxonomy_item(kind, found.id))
        return TaxonomyDeletion(
            kind=kind,
            id=found.id,
            name=found.name,
            document_count=count,
            also_deleted=below,
            message=f"{subject[0].upper()}{subject[1:]} was deleted: {effect}.",
        )

    async def _write[T](self, kind: WritableKind, write: Callable[[], Awaitable[T]]) -> T:
        """Run the write, then forget the resolver's rows of ``kind`` whatever happened.

        A refused storage path template gets the list of variables as a hint.
        """
        try:
            return await write()
        except Validation as error:
            if kind == "storage_path" and error.hint is None and PATH_FIELD in error.message:
                raise Validation(error.message, hint=PATH_HINT) from None
            raise
        finally:
            self._resolver.invalidate(kind)
