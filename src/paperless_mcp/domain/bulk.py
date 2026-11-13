"""Bulk edits: what a caller asks for (names), what the port sends (ids) and what comes back.

Documents are selected by explicit ids, at most ``MAX_DOCUMENTS`` per call. Every operation is a
typed model with its own parameters instead of an untyped parameter dict; the tool takes the
union, discriminated by ``op``. Services resolve names, diff against the current state and hand
the port an id-level change.

Output models are tool output schemas: a one-line class docstring each, and field descriptions
only where the name does not say enough.
"""

from typing import Annotated, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from paperless_mcp.domain.documents import NameOrId

# Most documents (or taxonomy items) one bulk call may name. It matches the largest search page,
# so a page of search_documents results can be passed on as it is.
MAX_DOCUMENTS: Final = 100
# Most rows a preview or a result lists as a sample of what changes.
SAMPLE_SIZE: Final = 10

type BulkKind = Literal["tag", "correspondent", "document_type", "storage_path"]
type Reference = Literal["correspondent", "document_type", "storage_path"]


class _Operation(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class AddTags(_Operation):
    """Add tags to every selected document; the tags they have stay."""

    op: Literal["add_tags"]
    tags: Annotated[
        list[NameOrId],
        Field(min_length=1, description="Tag names or ids to add (their parent tags come along)."),
    ]


class RemoveTags(_Operation):
    """Take tags off every selected document; the other tags stay."""

    op: Literal["remove_tags"]
    tags: Annotated[
        list[NameOrId],
        Field(min_length=1, description="Tag names or ids to remove (child tags go with them)."),
    ]


class ModifyTags(_Operation):
    """Add and remove tags in one step; the other tags stay."""

    op: Literal["modify_tags"]
    add: list[NameOrId] = Field(
        default_factory=list[NameOrId], description="Tag names or ids to add."
    )
    remove: list[NameOrId] = Field(
        default_factory=list[NameOrId], description="Tag names or ids to remove."
    )


class SetCorrespondent(_Operation):
    """Give every selected document one correspondent, or none."""

    op: Literal["set_correspondent"]
    correspondent: Annotated[
        NameOrId | None, Field(description="Name or id of the correspondent; null removes it.")
    ]


class SetDocumentType(_Operation):
    """Give every selected document one document type, or none."""

    op: Literal["set_document_type"]
    document_type: Annotated[
        NameOrId | None, Field(description="Name or id of the document type; null removes it.")
    ]


class SetStoragePath(_Operation):
    """Give every selected document one storage path, or none."""

    op: Literal["set_storage_path"]
    storage_path: Annotated[
        NameOrId | None, Field(description="Name or id of the storage path; null removes it.")
    ]


class ModifyCustomFields(_Operation):
    """Set or remove custom field values on every selected document; the other fields stay."""

    op: Literal["modify_custom_fields"]
    values: dict[str, JsonValue] = Field(
        default_factory=dict[str, JsonValue],
        description=(
            "Values to set, keyed by field name or id, as in update_document: string/longtext "
            "text; url with scheme; date 'YYYY-MM-DD'; boolean; integer; float; monetary "
            "'EUR12.50'; documentlink a list of document ids; select the option's label."
        ),
    )
    remove: list[NameOrId] = Field(
        default_factory=list[NameOrId],
        description="Custom field names or ids to take off the documents.",
    )


class DeleteDocuments(_Operation):
    """Move every selected document to the Paperless trash."""

    op: Literal["delete"]


class ReprocessDocuments(_Operation):
    """Run OCR and archiving again for every selected document."""

    op: Literal["reprocess"]


type BulkOperation = Annotated[
    AddTags
    | RemoveTags
    | ModifyTags
    | SetCorrespondent
    | SetDocumentType
    | SetStoragePath
    | ModifyCustomFields
    | DeleteDocuments
    | ReprocessDocuments,
    Field(
        discriminator="op",
        description="What to do to every selected document; op picks the kind of change.",
    ),
]


class DeleteItems(_Operation):
    """Delete the selected objects for good; documents lose what refers to them."""

    op: Literal["delete"]


class SetPermissions(_Operation):
    """Change the owner and who may view or change the selected objects."""

    op: Literal["set_permissions"]
    owner: Annotated[NameOrId | None, Field(description="Username or id of the new owner.")] = None
    clear_owner: Annotated[
        bool, Field(description="Remove the owner: every user may then use the objects.")
    ] = False
    view_users: Annotated[
        list[NameOrId] | None, Field(description="Users who may view (usernames or ids).")
    ] = None
    view_groups: Annotated[
        list[NameOrId] | None, Field(description="Groups who may view (names or ids).")
    ] = None
    change_users: Annotated[
        list[NameOrId] | None,
        Field(description="Users who may change, and so view (usernames or ids)."),
    ] = None
    change_groups: Annotated[
        list[NameOrId] | None,
        Field(description="Groups who may change, and so view (names or ids)."),
    ] = None
    merge: Annotated[
        bool,
        Field(
            description=(
                "true adds the given users and groups to the existing grants. false (default) "
                "replaces all grants: when any list is given, lists left out become empty."
            )
        ),
    ] = False


type TaxonomyOperation = Annotated[
    DeleteItems | SetPermissions,
    Field(
        discriminator="op",
        description="delete, or set_permissions (owner, and who may view or change).",
    ),
]


# The port speaks ids.


class TagsChange(BaseModel):
    """Add and remove tags (Paperless removes first, then adds; ancestors come with an add)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    add: tuple[int, ...] = ()
    remove: tuple[int, ...] = ()


class ReferenceChange(BaseModel):
    """Set a correspondent, document type or storage path; ``None`` removes it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    field: Reference
    value: int | None


class FieldsChange(BaseModel):
    """Set custom field values (stored form) and remove fields; the other fields stay."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    values: dict[int, JsonValue] = Field(default_factory=dict[int, JsonValue])
    remove: tuple[int, ...] = ()


class TrashDocuments(BaseModel):
    """Move the documents to the trash."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)


class ReprocessChange(BaseModel):
    """Queue the documents for reprocessing."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)


type DocumentChange = TagsChange | ReferenceChange | FieldsChange | TrashDocuments | ReprocessChange


class ObjectPermissions(BaseModel):
    """Who may view and change objects: user and group ids."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    view_users: tuple[int, ...] = ()
    view_groups: tuple[int, ...] = ()
    change_users: tuple[int, ...] = ()
    change_groups: tuple[int, ...] = ()


class PermissionsChange(BaseModel):
    """New owner and/or grants for objects; what was never set is left alone.

    ``owner`` set to ``None`` removes the owner. Without ``merge`` the grants replace all
    existing ones; with it they are added. Paperless then sets an owner only on objects that
    have none.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    owner: int | None = None
    permissions: ObjectPermissions | None = None
    merge: bool = False


# What comes back.


class DocumentSample(BaseModel):
    """A document the operation changes, with what changes on it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    title: str
    change: str
    web_url: Annotated[str, Field(description="Link to the document in the Paperless web UI.")]


class DocumentBulkReport(BaseModel):
    """A bulk edit of documents: the preview of a dry run, or what was done."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    operation: str
    summary: Annotated[str, Field(description="What the operation does, in words.")]
    dry_run: Annotated[bool, Field(description="true: a preview, nothing was changed.")]
    applied: Annotated[bool, Field(description="true: Paperless was asked to make the change.")]
    requested: Annotated[int, Field(description="How many documents were selected.")]
    affected_count: Annotated[
        int, Field(description="Documents that change (a preview) or changed (applied).")
    ]
    unchanged_count: Annotated[
        int, Field(description="Selected documents that already are as asked; they are left alone.")
    ]
    sample: Annotated[
        tuple[DocumentSample, ...],
        Field(description=f"The first {SAMPLE_SIZE} documents that change, in selection order."),
    ]
    also_changed: Annotated[
        tuple[DocumentSample, ...],
        Field(
            description=(
                "Documents that are not selected but change because document links are "
                f"mirrored on them (at most {SAMPLE_SIZE}); title '#id' if you cannot see it."
            )
        ),
    ]
    blocked: Annotated[
        tuple[DocumentSample, ...],
        Field(
            description=(
                "Selected documents you may not change. While there are any, Paperless "
                "refuses the whole batch and nothing changes."
            )
        ),
    ]
    message: str


class ItemSample(BaseModel):
    """A taxonomy object the operation touches."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    name: str
    document_count: Annotated[
        int, Field(description="Documents you can see that use it (before the operation).")
    ]


class TaxonomyBulkReport(BaseModel):
    """A bulk edit of taxonomy objects: the preview of a dry run, or what was done."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    kind: BulkKind
    operation: str
    summary: Annotated[str, Field(description="What the operation does, in words.")]
    dry_run: Annotated[bool, Field(description="true: a preview, nothing was changed.")]
    applied: Annotated[bool, Field(description="true: Paperless was asked to make the change.")]
    affected_count: Annotated[int, Field(description="Objects selected.")]
    sample: Annotated[
        tuple[ItemSample, ...],
        Field(description=f"The first {SAMPLE_SIZE} objects selected, in selection order."),
    ]
    also_deleted: Annotated[
        tuple[str, ...],
        Field(description="Delete of tags: child tags that go with them (names you can see)."),
    ]
    message: str
