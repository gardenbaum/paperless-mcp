"""Taxonomy: the named objects documents refer to by id, as compact rows.

Paperless filters each list by the user's permissions, so a row exists only if the user may see
the object; what a user may not see does not exist for this server.
"""

from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

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
        tuple[str, ...] | None, Field(description="Custom fields of type select: option labels.")
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
