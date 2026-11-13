"""Bulk deletion and permission changes of taxonomy objects, behind a preview and a confirmation.

Objects (tags, correspondents, document types, storage paths) are selected by name or id, at most
``MAX_DOCUMENTS`` per call. Like ``TaxonomyEditor`` the editor reads the current objects right
before it acts, not the resolver's cache, and forgets the resolver's rows of the kind afterwards,
also when the write failed (a timeout does not say whether it happened).

Both operations need ``confirm`` (decision-6, decision-15): a delete is final, and a permission
change can take objects away from other users, or from the caller. ``dry_run`` previews either.
Paperless checks every object first, so a refusal changes none of them.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

from paperless_mcp.domain.bulk import (
    MAX_DOCUMENTS,
    SAMPLE_SIZE,
    DeleteItems,
    ItemSample,
    ObjectPermissions,
    PermissionsChange,
    SetPermissions,
    TaxonomyBulkReport,
)
from paperless_mcp.domain.errors import (
    ConfirmationRequired,
    PermissionDenied,
    Validation,
    listed,
    quoted,
)
from paperless_mcp.services.tag_hierarchy import descendants
from paperless_mcp.services.taxonomy import pick
from paperless_mcp.services.taxonomy_edits import LABELS
from paperless_mcp.services.taxonomy_edits import documents as count_documents

if TYPE_CHECKING:
    from collections.abc import Sequence

    from paperless_mcp.domain.bulk import BulkKind, TaxonomyOperation
    from paperless_mcp.domain.documents import NameOrId
    from paperless_mcp.domain.ports import BulkWriter, TaxonomyReader
    from paperless_mcp.domain.taxonomy import TaxonomyItem
    from paperless_mcp.services.taxonomy import TaxonomyResolver

# What deleting objects does to the documents that use them, by kind (a clause of the effect).
USES: Final[dict[str, str]] = {
    "tag": "{they} {are} removed from {their} documents",
    "correspondent": "the documents that use {them} lose their correspondent",
    "document_type": "the documents that use {them} lose their document type",
    "storage_path": "the documents that use {them} lose their storage path",
}
DRY_RUN: Final = "Dry run, nothing was changed."
TO_GO_AHEAD: Final = "To go ahead, call again without dry_run and with confirm=true."
MERGE_OWNER_HINT = (
    "Paperless then sets it only on objects without an owner and never removes one; "
    "set the owner in a call without merge."
)
# The grant lists of SetPermissions with the kind of principal each holds.
GRANT_LISTS: Final[tuple[tuple[str, Literal["user", "group"]], ...]] = (
    ("view_users", "user"),
    ("view_groups", "group"),
    ("change_users", "user"),
    ("change_groups", "group"),
)


def _numeric(value: NameOrId) -> bool:
    return isinstance(value, int) or (value.strip().isascii() and value.strip().isdigit())


def _plural(count: int, noun: str) -> str:
    return noun if count == 1 else f"{noun}s"


@dataclass(frozen=True, slots=True)
class _Principals:
    """Users or groups as ids, with the words that name them in a message."""

    ids: tuple[int, ...]
    names: tuple[str, ...]


class BulkTaxonomyEditor:
    """Deletes or re-permissions many taxonomy objects at once."""

    def __init__(
        self, reader: TaxonomyReader, writer: BulkWriter, resolver: TaxonomyResolver
    ) -> None:
        """Read the current objects through ``reader``, write through ``writer``.

        ``resolver`` resolves users and groups and is the cache every write invalidates.
        """
        self._reader: TaxonomyReader = reader
        self._writer: BulkWriter = writer
        self._resolver: TaxonomyResolver = resolver

    async def edit(
        self,
        kind: BulkKind,
        items: Sequence[NameOrId],
        operation: TaxonomyOperation,
        *,
        dry_run: bool,
        confirm: bool,
    ) -> TaxonomyBulkReport:
        """Preview (``dry_run``) or apply ``operation`` to the objects ``items`` name.

        Raises:
            Validation: The selection is empty or over ``MAX_DOCUMENTS``, or the permission
                change is empty or contradicts itself.
            NotFound: An object, user or group matches nothing.
            Ambiguous: A name matches more than one object.
            ConfirmationRequired: ``confirm`` is false. Nothing was changed.
            PermissionDenied: The user may not change one of the objects, or may not list users
                or groups to resolve a name; Paperless then changed none.
        """
        if not 1 <= len(items) <= MAX_DOCUMENTS:
            message = f"Select between 1 and {MAX_DOCUMENTS} items per call."
            hint = "Use list_taxonomy to find the ones you mean, and split the work."
            raise Validation(message, hint=hint)
        grants = None if isinstance(operation, DeleteItems) else await self._grants(operation)
        rows = await self._reader.list_taxonomy(kind)
        chosen = list({row.id: row for row in (pick(kind, rows, item) for item in items)}.values())
        label = LABELS[kind]
        subject = f"{len(chosen)} {_plural(len(chosen), label)} {listed([r.name for r in chosen])}"
        below = _below(kind, rows, chosen) if grants is None else []
        if grants is None:
            tail = f"{subject} for good: {_uses(kind, chosen, below)}"
            what, done = f"delete {tail}", f"Deleted {tail}."
        else:
            what = f"{grants[1]} on {subject}"
            done = f"Applied: {what}."
        if not (dry_run or confirm):
            raise ConfirmationRequired.for_effect(what)
        if not dry_run:
            await self._write(kind, [row.id for row in chosen], grants and grants[0])
        return TaxonomyBulkReport(
            kind=kind,
            operation=operation.op,
            summary=(
                f"delete the selected {label}s for good"
                if grants is None
                else f"set permissions on the selected {label}s"
            ),
            dry_run=dry_run,
            applied=not dry_run,
            affected_count=len(chosen),
            sample=tuple(
                ItemSample(id=row.id, name=row.name, document_count=row.document_count or 0)
                for row in chosen[:SAMPLE_SIZE]
            ),
            also_deleted=tuple(row.name for row in below),
            message=f"{DRY_RUN} Confirming would {what}. {TO_GO_AHEAD}" if dry_run else done,
        )

    async def _write(
        self, kind: BulkKind, ids: list[int], change: PermissionsChange | None
    ) -> None:
        """Delete the objects, or set their permissions; forget the kind whatever happened."""
        try:
            if change is None:
                await self._writer.delete_taxonomy_items(kind, ids)
            else:
                await self._writer.set_taxonomy_permissions(kind, ids, change)
        finally:
            self._resolver.invalidate(kind)

    async def _grants(self, operation: SetPermissions) -> tuple[PermissionsChange, str]:
        """The id-level change and a sentence for it (without the objects)."""
        given = any(getattr(operation, name) is not None for name, _ in GRANT_LISTS)
        if operation.owner is not None and operation.clear_owner:
            message = "Give owner or clear_owner, not both."
            raise Validation(message, hint="clear_owner=true removes the owner.")
        owner_given = operation.owner is not None or operation.clear_owner
        if not (owner_given or given):
            message = "Nothing to change: no owner and no grants were given."
            hint = "Pass owner, clear_owner or a list of users or groups."
            raise Validation(message, hint=hint)
        if operation.merge and owner_given:
            message = "An owner cannot be set while merging grants."
            raise Validation(message, hint=MERGE_OWNER_HINT)
        fields: dict[str, object] = {"merge": operation.merge}
        owner = ""
        if operation.owner is not None:
            who = await self._principals("user", [operation.owner])
            fields["owner"], owner = who.ids[0], f"set the owner to {who.names[0]}"
        if operation.clear_owner:
            fields["owner"] = None
            owner = "remove the owner (every user may then use the objects)"
        sentence = owner
        if given:
            shown: list[str] = []
            lists: dict[str, tuple[int, ...]] = {}
            for name, principal in GRANT_LISTS:
                who = await self._principals(principal, getattr(operation, name) or [])
                lists[name] = who.ids
                if getattr(operation, name) is not None or not operation.merge:
                    shown.append(f"{name.replace('_', ' ')} {', '.join(who.names) or 'none'}")
            how = (
                "added to the existing grants"
                if operation.merge
                else "replacing all existing grants"
            )
            fields["permissions"] = ObjectPermissions.model_validate(lists)
            grants = f"the grants ({', '.join(shown)}; {how})"
            sentence = (
                f"{owner} and {grants}"
                if not owner.startswith("remove")
                else f"{owner} and set {grants}"
            )
            sentence = sentence if owner else f"set {grants}"
        return PermissionsChange.model_validate(fields), sentence

    async def _principals(
        self, kind: Literal["user", "group"], values: Sequence[NameOrId]
    ) -> _Principals:
        """Ids and names of users or groups, checked against the list the user may see.

        An id is passed on unchecked only when the user may not list that kind.
        """
        ids: list[int] = []
        names: list[str] = []
        for value in values:
            try:
                found = await self._resolver.resolve(kind, value)
            except PermissionDenied as error:
                if not _numeric(value):
                    hint = f"Pass the {kind} id instead of the name."
                    raise PermissionDenied(error.message, hint=hint) from None
                ids.append(int(value))  # the user may not list them: Paperless decides
                names.append(f"#{int(value)}")
                continue
            ids.append(found.id)
            names.append(quoted(found.name))
        return _Principals(ids=tuple(ids), names=tuple(names))


def _below(
    kind: BulkKind, rows: list[TaxonomyItem], chosen: list[TaxonomyItem]
) -> list[TaxonomyItem]:
    """The child tags, at any depth, that go with the chosen tags without being chosen."""
    if kind != "tag":
        return []
    parents = {row.id: row.parent for row in rows}
    gone = {n for row in chosen for n in descendants(row.id, parents)}
    picked = {row.id for row in chosen}
    return sorted((row for row in rows if row.id in gone - picked), key=lambda row: row.name)


def _counted(rows: list[TaxonomyItem]) -> str:
    return listed([f"{row.name} ({count_documents(row.document_count or 0)})" for row in rows])


def _uses(kind: BulkKind, chosen: list[TaxonomyItem], below: list[TaxonomyItem]) -> str:
    """What deleting the objects does to documents (and, for tags, child tags), as a clause."""
    many = len(chosen) != 1
    words = {
        "they": "they" if many else "it",
        "are": "are" if many else "is",
        "their": "their" if many else "its",
        "them": "them" if many else "it",
    }
    effect = f"{USES[kind].format(**words)} ({_counted(chosen)})"
    if kind == "tag":
        if below:
            effect += (
                f"; {words['their']} child tags {_counted(below)} are deleted with {words['them']}"
            )
        effect += (
            f"; child tags you cannot see (other users') are deleted with {words['them']} as well"
        )
    return effect
