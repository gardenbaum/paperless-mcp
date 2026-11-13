"""The rule type table of saved views against the vendored Paperless 3.4.2 schema."""

import json
import re
from pathlib import Path
from typing import cast

import pytest

from paperless_mcp.domain.views import RULE_TYPES, ViewValues

SCHEMA = Path(__file__).parents[2] / "docs" / "vendor" / "paperless" / "openapi.json"

type Json = dict[str, object]


def member(value: object, key: str) -> object:
    assert isinstance(value, dict)
    return cast("Json", value)[key]


def test_the_rule_types_are_those_of_the_vendored_schema() -> None:
    document = cast("object", json.loads(SCHEMA.read_text()))
    enum = member(member(member(document, "components"), "schemas"), "RuleTypeEnum")
    described = {
        int(number): label
        for number, label in cast(
            "list[tuple[str, str]]",
            re.findall(r"\* `(\d+)` - ([^\n]+)", str(member(enum, "description"))),
        )
    }

    assert described == RULE_TYPES
    assert sorted(RULE_TYPES) == member(enum, "enum")


def test_only_the_sort_field_and_the_page_size_can_be_given_as_none() -> None:
    assert ViewValues(sort_field=None, page_size=None).model_fields_set == {
        "sort_field",
        "page_size",
    }


@pytest.mark.parametrize("name", ["name", "filter_rules", "sort_reverse"])
def test_every_other_field_has_a_value_once_it_is_given(name: str) -> None:
    with pytest.raises(ValueError, match=f"{name} cannot be cleared"):
        _ = ViewValues.model_validate({name: None})
