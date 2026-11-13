"""TaxonomyValues: the fields a write sets, and which of them can be cleared."""

import pytest

from paperless_mcp.domain.taxonomy import TaxonomyValues


def test_only_the_parent_can_be_given_as_none() -> None:
    assert TaxonomyValues(parent=None).model_fields_set == {"parent"}


@pytest.mark.parametrize("name", ["name", "colour", "match", "path", "select_options"])
def test_every_other_field_has_a_value_once_it_is_given(name: str) -> None:
    with pytest.raises(ValueError, match=f"{name} cannot be cleared"):
        _ = TaxonomyValues.model_validate({name: None})
