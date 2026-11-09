"""Smoke tests of the E2E stack itself: version, seeding, and its idempotence."""

import pytest

from .conftest import Seeded, Stack

pytestmark = pytest.mark.e2e


@pytest.mark.decision("decision-2")
def test_instance_reports_api_version_10(stack: Stack) -> None:
    response = stack.admin.get("/api/tags/")
    assert response.headers["X-Api-Version"] == "10"
    assert response.headers["X-Version"] == "3.4.2"


def test_seeding_is_idempotent(stack: Stack, reseed: Seeded) -> None:
    assert reseed == stack.seeded
    assert stack.admin.get("/api/documents/").json()["count"] == 2


def test_seeded_taxonomy_and_all_custom_field_types(stack: Stack) -> None:
    child = stack.admin.get(f"/api/tags/{stack.seeded.child_tag}/").json()
    assert child["parent"] == stack.seeded.parent_tag
    document = stack.admin.get(f"/api/documents/{stack.seeded.public_document}/").json()
    assert {v["field"] for v in document["custom_fields"]} == set(
        stack.seeded.custom_fields.values()
    )
    assert len(stack.seeded.custom_fields) == 10


def test_restricted_user_cannot_see_private_objects(stack: Stack) -> None:
    ids = {d["id"] for d in stack.reader.get("/api/documents/").json()["results"]}
    assert ids == {stack.seeded.public_document}
    assert stack.reader.get(f"/api/documents/{stack.seeded.private_document}/").status_code == 404
    assert stack.reader.post("/api/tags/", json={"name": "nope"}).status_code == 403
