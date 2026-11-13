"""What a caller wants created or changed in the taxonomy, with names where Paperless wants ids."""

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from paperless_mcp.domain.documents import NameOrId
from paperless_mcp.domain.taxonomy import (
    DataType,
    MatchingAlgorithm,
)


class TaxonomyFields(BaseModel):
    """The settings of a tag, correspondent, document type, storage path or custom field.

    A setting left out is not sent. Which settings a kind takes is checked by
    ``TaxonomyEditor``: the matching rule belongs to every kind but custom fields, colour,
    parent and inbox flag to tags, the path template to storage paths.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    matching_algorithm: MatchingAlgorithm | None = None
    match: str | None = None
    case_insensitive: bool | None = None
    colour: str | None = None
    parent: NameOrId | None = None
    is_inbox_tag: bool | None = None
    path: str | None = None


class TaxonomyCreate(TaxonomyFields):
    """A new object; ``data_type`` and ``select_options`` are for custom fields."""

    name: str
    data_type: DataType | None = None
    select_options: tuple[str, ...] = ()


class TaxonomyEdit(TaxonomyFields):
    """Changes to an object; select options are added, renamed or removed, never replaced."""

    name: str | None = None
    clear_parent: bool = False
    options_add: tuple[str, ...] = ()
    options_rename: dict[str, str] = Field(default_factory=dict[str, str])
    options_remove: tuple[str, ...] = ()
    confirm: bool = False
