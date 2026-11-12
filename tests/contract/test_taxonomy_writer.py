"""Contract of TaxonomyWriter: the same assertions against the fake and live Paperless 3.4.2.

The live backend deletes every tag whose name starts with `SCRATCH_TAG` when the test ends.
"""

import uuid
from typing import TYPE_CHECKING

import pytest

from contract.conftest import SCRATCH_TAG
from paperless_mcp.domain.errors import PermissionDenied, Validation

if TYPE_CHECKING:
    from contract.conftest import Backend

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-6")]

CONTRACT = "TaxonomyWriter.create_tag"


def new_name() -> str:
    return f"{SCRATCH_TAG}{uuid.uuid4().hex}"


@pytest.mark.contract(CONTRACT)
async def test_a_created_tag_is_listed_with_no_documents_and_no_parent(backend: Backend) -> None:
    name = new_name()

    created = await backend.admin.taxonomy_writes.create_tag(name)

    assert created.name == name
    assert (created.document_count, created.parent) == (0, None)
    listed = {item.name: item for item in await backend.admin.taxonomy.list_taxonomy("tag")}
    assert listed[name].id == created.id


@pytest.mark.contract(CONTRACT)
async def test_a_created_tag_belongs_to_its_creator(backend: Backend) -> None:
    name = new_name()

    _ = await backend.admin.taxonomy_writes.create_tag(name)

    assert name not in {item.name for item in await backend.reader.taxonomy.list_taxonomy("tag")}


@pytest.mark.contract(CONTRACT)
async def test_the_same_name_twice_is_rejected(backend: Backend) -> None:
    name = new_name()
    _ = await backend.admin.taxonomy_writes.create_tag(name)

    with pytest.raises(Validation):
        _ = await backend.admin.taxonomy_writes.create_tag(name)


@pytest.mark.contract(CONTRACT)
@pytest.mark.parametrize("name", ["", "  ", "x" * 129])
async def test_a_blank_or_too_long_name_is_rejected(backend: Backend, name: str) -> None:
    with pytest.raises(Validation):
        _ = await backend.admin.taxonomy_writes.create_tag(name)


@pytest.mark.contract(CONTRACT)
async def test_a_user_who_may_not_add_tags_is_denied(backend: Backend) -> None:
    with pytest.raises(PermissionDenied):
        _ = await backend.reader.taxonomy_writes.create_tag(new_name())
