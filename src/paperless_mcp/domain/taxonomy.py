"""Taxonomy: the named objects documents refer to by id, as compact rows.

Paperless filters each list by the user's permissions, so a row exists only if the user may see
the object; what a user may not see does not exist for this server.
"""

from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

type TaxonomyKind = Literal[
    "tag",
    "correspondent",
    "document_type",
    "storage_path",
    "custom_field",
    "saved_view",
    "user",
    "group",
]


# The kinds tools may create, change and delete: Paperless owns the rest (views, users, groups).
type WritableKind = Literal["tag", "correspondent", "document_type", "storage_path", "custom_field"]
type MatchingAlgorithm = Literal["none", "any", "all", "literal", "regex", "fuzzy", "auto"]
type DataType = Literal[
    "string",
    "url",
    "date",
    "boolean",
    "integer",
    "float",
    "monetary",
    "documentlink",
    "select",
    "longtext",
]


class SelectOption(BaseModel):
    """An option of a select custom field; documents store its id as the value."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: str
    label: str


class TaxonomyItem(BaseModel):
    """One taxonomy object; fields that do not apply to its kind are null."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    name: Annotated[str, Field(description="The name; the username for users.")]
    document_count: Annotated[
        int | None,
        Field(description="Documents the user may see that use it; null for views, users, groups."),
    ]
    colour: Annotated[str | None, Field(description="Tags: the colour, e.g. #a6cee3.")] = None
    parent: Annotated[int | None, Field(description="Tags: id of the parent tag.")] = None
    data_type: Annotated[str | None, Field(description="Custom fields: the data type.")] = None
    select_options: Annotated[
        tuple[SelectOption, ...] | None,
        Field(description="Custom fields of type select: the options, ids and labels."),
    ] = None


class TaxonomyListing(BaseModel):
    """Taxonomy rows of one kind, at most a page of them."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    kind: TaxonomyKind
    count: Annotated[int, Field(description="How many objects match, also beyond the items.")]
    truncated: Annotated[
        bool, Field(description="More match than the items hold; narrow with name_contains.")
    ]
    items: tuple[TaxonomyItem, ...]


class TaxonomyDetail(TaxonomyItem):
    """A taxonomy object with its automatic-matching rule, as a write answers it.

    Fields that do not apply to the kind are null: the matching rule belongs to every kind but
    custom fields, the inbox flag to tags, the path template to storage paths.
    """

    matching_algorithm: Annotated[
        MatchingAlgorithm | None,
        Field(description="How Paperless assigns it to new documents by itself; none: never."),
    ] = None
    match: Annotated[str | None, Field(description="The text or pattern the algorithm uses.")] = (
        None
    )
    case_insensitive: Annotated[
        bool | None, Field(description="Whether matching ignores case.")
    ] = None
    is_inbox_tag: Annotated[
        bool | None, Field(description="Tags: documents with it count as the inbox.")
    ] = None
    path: Annotated[str | None, Field(description="Storage paths: the path template.")] = None


class OptionInput(BaseModel):
    """A select option as a write sends it: an existing one by id, a new one without."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: str | None = None
    label: str


class TaxonomyValues(BaseModel):
    """The fields of a taxonomy object a write sets, ids only; the port sends exactly those.

    A field that was never given is left alone; only ``parent`` can be given as ``None`` (the
    tag becomes a root). ``select_options`` is the whole new list: options with an ``id`` keep
    it (and the documents that use them), those without get a new one, options left out are
    removed.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    name: str | None = None
    matching_algorithm: MatchingAlgorithm | None = None
    match: str | None = None
    case_insensitive: bool | None = None
    colour: str | None = None
    parent: int | None = None
    is_inbox_tag: bool | None = None
    path: str | None = None
    data_type: DataType | None = None
    select_options: tuple[OptionInput, ...] | None = None

    @model_validator(mode="after")
    def _only_parent_clears(self) -> TaxonomyValues:
        """Every field but the parent has a value once it is given."""
        for name in self.model_fields_set - {"parent"}:
            if getattr(self, name) is None:
                message = f"{name} cannot be cleared"
                raise ValueError(message)
        return self


class DeletedTag(BaseModel):
    """A child tag that goes with its parent, and how many documents lose it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    name: str
    document_count: int


class TaxonomyDeletion(BaseModel):
    """The outcome of delete_taxonomy_item: what happened, or what confirm would do."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    kind: WritableKind
    id: int
    name: str
    deleted: Annotated[bool, Field(description="false: nothing was deleted, see message.")]
    document_count: Annotated[
        int, Field(description="Documents you can see that use it (not those of its child tags).")
    ]
    also_deleted: Annotated[
        tuple[DeletedTag, ...],
        Field(
            description=(
                "Tags: the child tags you can see that are deleted with it, with their document "
                "counts. Child tags you cannot see (other users') are deleted too."
            )
        ),
    ] = ()
    message: Annotated[str, Field(description="The effect of the deletion, and how to confirm.")]
