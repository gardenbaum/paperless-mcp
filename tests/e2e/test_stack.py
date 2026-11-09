"""Smoke tests of the E2E stack itself: version, seeding, and its idempotence."""

import pytest

from e2e.helpers import Seeded, Stack, num, obj, parse, results, seq

pytestmark = pytest.mark.e2e


@pytest.mark.decision("decision-2")
def test_instance_reports_api_version_10(stack: Stack) -> None:
    response = stack.admin.get("/api/tags/")
    assert response.headers["X-Api-Version"] == "10"
    assert response.headers["X-Version"] == "3.4.2"


def test_seeding_is_idempotent(stack: Stack, reseed: Seeded) -> None:
    assert reseed == stack.seeded
    assert obj(parse(stack.admin.get("/api/documents/")))["count"] == 2


def test_seeded_taxonomy_and_all_custom_field_types(stack: Stack) -> None:
    child = obj(parse(stack.admin.get(f"/api/tags/{stack.seeded.child_tag}/")))
    assert child["parent"] == stack.seeded.parent_tag
    document = obj(parse(stack.admin.get(f"/api/documents/{stack.seeded.public_document}/")))
    assert {num(obj(v)["field"]) for v in seq(document["custom_fields"])} == set(
        stack.seeded.custom_fields.values()
    )
    assert len(stack.seeded.custom_fields) == 10


def test_restricted_user_cannot_see_private_objects(stack: Stack) -> None:
    ids = {num(d["id"]) for d in results(stack.reader.get("/api/documents/"))}
    assert ids == {stack.seeded.public_document}
    assert stack.reader.get(f"/api/documents/{stack.seeded.private_document}/").status_code == 404
    assert stack.reader.post("/api/tags/", json={"name": "nope"}).status_code == 403
