"""BulkWriter on ``POST /api/documents/bulk_edit/`` and ``POST /api/bulk_edit_objects/``.

Both endpoints answer ``{"result": "OK"}`` once the change is made (probed on 3.4.2: tag, field
and reference changes are visible on return; a reprocess is queued and its task is not
returned). They validate the whole request first, so a refused batch changes nothing. Posts are
never retried by the client.
"""

from typing import TYPE_CHECKING, Final

from pydantic import BaseModel

from paperless_mcp.domain.bulk import (
    FieldsChange,
    ReferenceChange,
    TagsChange,
    TrashDocuments,
)
from paperless_mcp.domain.errors import PermissionDenied

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import JsonValue

    from paperless_mcp.domain.bulk import BulkKind, DocumentChange, PermissionsChange
    from paperless_mcp.paperless.client import PaperlessClient

DOCUMENTS = "/api/documents/bulk_edit/"
OBJECTS = "/api/bulk_edit_objects/"
OBJECT_TYPES: Final[dict[BulkKind, str]] = {
    "tag": "tags",
    "correspondent": "correspondents",
    "document_type": "document_types",
    "storage_path": "storage_paths",
}


class _Result(BaseModel):
    result: str


def _method(change: DocumentChange) -> tuple[str, dict[str, JsonValue]]:
    """Paperless's ``method`` and ``parameters`` for ``change``."""
    match change:
        case TagsChange():
            return "modify_tags", {
                "add_tags": list(change.add),
                "remove_tags": list(change.remove),
            }
        case ReferenceChange():
            return f"set_{change.field}", {change.field: change.value}
        case FieldsChange():
            # The dict form keeps the other fields; the list form would empty a field's value.
            added: dict[str, JsonValue] = {str(n): v for n, v in change.values.items()}
            return "modify_custom_fields", {
                "add_custom_fields": added,
                "remove_custom_fields": list(change.remove),
            }
        case TrashDocuments():
            return "delete", {}
        case _:
            return "reprocess", {}


DOCUMENTS_MESSAGE = (
    "Paperless refused the bulk edit; it checks every selected document first, so nothing "
    "was changed."
)
DOCUMENTS_HINT = (
    "Every selected document needs change permission for you; deleting also needs "
    "you to own it (or it to have no owner) and the delete permission."
)
OBJECTS_MESSAGE = (
    "Paperless refused the bulk edit; it checks every selected object first, so nothing "
    "was changed."
)
OBJECTS_HINT = (
    "You need the permission for the kind and must own every selected object (or it "
    "must have no owner); a tag's child tags count too."
)


def documents_refused() -> PermissionDenied:
    """The error for a 403 of the document endpoint (Paperless says 'Insufficient permissions')."""
    return PermissionDenied(DOCUMENTS_MESSAGE, hint=DOCUMENTS_HINT)


def objects_refused() -> PermissionDenied:
    """The error for a 403 of the object endpoint."""
    return PermissionDenied(OBJECTS_MESSAGE, hint=OBJECTS_HINT)


class PaperlessBulkWriter:
    """``BulkWriter`` over the Paperless REST API."""

    def __init__(self, client: PaperlessClient) -> None:
        """Write through ``client``."""
        self._client: PaperlessClient = client

    async def bulk_edit_documents(
        self, document_ids: Sequence[int], change: DocumentChange
    ) -> None:
        """See ``BulkWriter.bulk_edit_documents``.

        A 403 means the global change permission is missing, or one document is not changeable
        by the user (also a hidden one: it is not told apart); deleting needs ownership of every
        document and the delete permission.
        """
        method, parameters = _method(change)
        body: dict[str, JsonValue] = {
            "documents": list(document_ids),
            "method": method,
            "parameters": parameters,
        }
        try:
            _ = await self._client.call("POST", DOCUMENTS, _Result, json=body)
        except PermissionDenied:
            raise documents_refused() from None

    async def delete_taxonomy_items(self, kind: BulkKind, item_ids: Sequence[int]) -> None:
        """See ``BulkWriter.delete_taxonomy_items``."""
        body: dict[str, JsonValue] = {
            "objects": list(item_ids),
            "object_type": OBJECT_TYPES[kind],
            "operation": "delete",
        }
        await self._objects(body)

    async def set_taxonomy_permissions(
        self, kind: BulkKind, item_ids: Sequence[int], change: PermissionsChange
    ) -> None:
        """See ``BulkWriter.set_taxonomy_permissions``.

        An owner is sent only when ``change`` sets one (``None`` removes it). With ``merge``
        Paperless sets an owner only on objects that have none.
        """
        body: dict[str, JsonValue] = {
            "objects": list(item_ids),
            "object_type": OBJECT_TYPES[kind],
            "operation": "set_permissions",
            "merge": change.merge,
        }
        if "owner" in change.model_fields_set:
            body["owner"] = change.owner
        if change.permissions is not None:
            grants = change.permissions
            body["permissions"] = {
                "view": {"users": list(grants.view_users), "groups": list(grants.view_groups)},
                "change": {
                    "users": list(grants.change_users),
                    "groups": list(grants.change_groups),
                },
            }
        await self._objects(body)

    async def _objects(self, body: dict[str, JsonValue]) -> None:
        try:
            _ = await self._client.call("POST", OBJECTS, _Result, json=body)
        except PermissionDenied:
            raise objects_refused() from None
