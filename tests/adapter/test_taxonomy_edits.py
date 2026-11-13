"""PaperlessTaxonomyWriter's create, update and delete on the Paperless 3.4.2 wire format."""

import json
from typing import TYPE_CHECKING, cast

import httpx2
import pytest

from adapter.conftest import tag
from paperless_mcp.domain.errors import NotFound, PaperlessMcpError, PermissionDenied, Validation
from paperless_mcp.domain.taxonomy import OptionInput, SelectOption, TaxonomyValues
from paperless_mcp.paperless.taxonomy import PaperlessTaxonomyWriter

if TYPE_CHECKING:
    from adapter.conftest import ClientFactory
    from paperless_mcp.domain.taxonomy import WritableKind

pytestmark = pytest.mark.anyio

type Json = dict[str, object]

PATHS: dict[str, str] = {
    "tag": "/api/tags/",
    "correspondent": "/api/correspondents/",
    "document_type": "/api/document_types/",
    "storage_path": "/api/storage_paths/",
    "custom_field": "/api/custom_fields/",
}
OPTIONS: list[Json] = [{"label": "alpha", "id": "aaaa"}, {"label": "beta", "id": "bbbb"}]


def wire(kind: str, number: int, name: str, **fields: object) -> Json:
    """A row of ``kind`` as 3.4.2 serialises it."""
    owned: Json = {
        "id": number,
        "slug": name,
        "name": name,
        "match": "",
        "matching_algorithm": 1,
        "is_insensitive": True,
        "owner": 2,
        "user_can_change": True,
    }
    rows: dict[str, Json] = {
        "tag": {
            **tag(number, name),
            "owner": 2,
        },
        "correspondent": owned,
        "document_type": owned,
        "storage_path": {**owned, "path": "x/{{ title }}"},
        "custom_field": {"id": number, "name": name, "data_type": "string", "extra_data": None},
    }
    return {**rows[kind], "document_count": 0, **fields}


class Recording:
    """Answers each request with the next scripted response and records method, path, body."""

    def __init__(self, *answers: httpx2.Response) -> None:
        self.answers: list[httpx2.Response] = list(answers)
        self.sent: list[tuple[str, str, Json | None, dict[str, str]]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = cast("Json", json.loads(request.content)) if request.content else None
        self.sent.append((request.method, request.url.path, body, dict(request.url.params)))
        return self.answers.pop(0)


def created(kind: str, **fields: object) -> httpx2.Response:
    """The answer to a create: 3.4.2 sends no ``document_count``."""
    row = wire(kind, 7, "New", **fields)
    del row["document_count"]
    return httpx2.Response(201, json=row)


def updated(kind: str, **fields: object) -> httpx2.Response:
    return httpx2.Response(200, json=wire(kind, 7, "New", document_count=3, **fields))


@pytest.mark.parametrize("kind", ["tag", "correspondent", "document_type"])
async def test_a_new_object_never_matches_automatically_unless_told(
    make_client: ClientFactory, kind: WritableKind
) -> None:
    server = Recording(created(kind, matching_algorithm=0))

    item = await PaperlessTaxonomyWriter(make_client(server)).create_taxonomy_item(
        kind, "New", TaxonomyValues()
    )

    assert server.sent == [("POST", PATHS[kind], {"name": "New", "matching_algorithm": 0}, {})]
    assert (item.id, item.name, item.document_count) == (7, "New", 0)
    assert item.matching_algorithm == "none"


async def test_every_tag_setting_has_its_paperless_name(make_client: ClientFactory) -> None:
    server = Recording(
        created(
            "tag",
            color="#ff0000",
            parent=3,
            matching_algorithm=4,
            match="^inv",
            is_insensitive=False,
            is_inbox_tag=True,
        )
    )
    values = TaxonomyValues(
        colour="#ff0000",
        parent=3,
        matching_algorithm="regex",
        match="^inv",
        case_insensitive=False,
        is_inbox_tag=True,
    )

    item = await PaperlessTaxonomyWriter(make_client(server)).create_taxonomy_item(
        "tag", "New", values
    )

    assert server.sent[0][2] == {
        "name": "New",
        "color": "#ff0000",
        "parent": 3,
        "matching_algorithm": 4,
        "match": "^inv",
        "is_insensitive": False,
        "is_inbox_tag": True,
    }
    assert (item.colour, item.parent, item.matching_algorithm, item.match) == (
        "#ff0000",
        3,
        "regex",
        "^inv",
    )
    assert (item.case_insensitive, item.is_inbox_tag, item.path) == (False, True, None)


@pytest.mark.parametrize(
    ("name", "number"),
    [("none", 0), ("any", 1), ("all", 2), ("literal", 3), ("regex", 4), ("fuzzy", 5), ("auto", 6)],
)
async def test_each_matching_algorithm_has_its_number(
    make_client: ClientFactory, name: str, number: int
) -> None:
    server = Recording(created("correspondent", matching_algorithm=number))
    values = TaxonomyValues.model_validate({"matching_algorithm": name})

    item = await PaperlessTaxonomyWriter(make_client(server)).create_taxonomy_item(
        "correspondent", "New", values
    )

    assert server.sent[0][2] == {"name": "New", "matching_algorithm": number}
    assert item.matching_algorithm == name


async def test_a_storage_path_carries_its_template(make_client: ClientFactory) -> None:
    server = Recording(created("storage_path", path="{{ created_year }}/{{ title }}"))

    item = await PaperlessTaxonomyWriter(make_client(server)).create_taxonomy_item(
        "storage_path", "New", TaxonomyValues(path="{{ created_year }}/{{ title }}")
    )

    assert server.sent[0][2] == {
        "name": "New",
        "matching_algorithm": 0,
        "path": "{{ created_year }}/{{ title }}",
    }
    assert item.path == "{{ created_year }}/{{ title }}"


async def test_a_select_field_is_created_with_labels_and_answers_with_ids(
    make_client: ClientFactory,
) -> None:
    server = Recording(
        created("custom_field", data_type="select", extra_data={"select_options": OPTIONS})
    )
    values = TaxonomyValues(
        data_type="select",
        select_options=(OptionInput(label="alpha"), OptionInput(label="beta")),
    )

    item = await PaperlessTaxonomyWriter(make_client(server)).create_taxonomy_item(
        "custom_field", "New", values
    )

    assert server.sent[0][2] == {
        "name": "New",
        "data_type": "select",
        "extra_data": {"select_options": [{"label": "alpha"}, {"label": "beta"}]},
    }
    assert item.data_type == "select"
    assert item.select_options == (
        SelectOption(id="aaaa", label="alpha"),
        SelectOption(id="bbbb", label="beta"),
    )
    assert item.matching_algorithm is None
    assert item.document_count == 0


async def test_a_plain_custom_field_sends_no_extra_data(make_client: ClientFactory) -> None:
    server = Recording(created("custom_field"))

    item = await PaperlessTaxonomyWriter(make_client(server)).create_taxonomy_item(
        "custom_field", "New", TaxonomyValues(data_type="integer")
    )

    assert server.sent[0][2] == {"name": "New", "data_type": "integer"}
    assert item.select_options is None


async def test_only_the_fields_an_update_sets_are_sent_and_the_count_comes_back(
    make_client: ClientFactory,
) -> None:
    server = Recording(updated("tag", color="#00ff00"))

    item = await PaperlessTaxonomyWriter(make_client(server)).update_taxonomy_item(
        "tag", 7, TaxonomyValues(colour="#00ff00")
    )

    assert server.sent == [("PATCH", "/api/tags/7/", {"color": "#00ff00"}, {})]
    assert (item.colour, item.document_count) == ("#00ff00", 3)


async def test_an_update_can_make_a_tag_a_root_and_set_everything_else(
    make_client: ClientFactory,
) -> None:
    server = Recording(updated("tag"), updated("tag"))
    writer = PaperlessTaxonomyWriter(make_client(server))

    _ = await writer.update_taxonomy_item("tag", 7, TaxonomyValues(parent=None))
    _ = await writer.update_taxonomy_item(
        "tag",
        7,
        TaxonomyValues(
            name="Renamed", is_inbox_tag=False, match="", matching_algorithm="any", parent=2
        ),
    )

    assert [body for _, _, body, _ in server.sent] == [
        {"parent": None},
        {
            "name": "Renamed",
            "is_inbox_tag": False,
            "match": "",
            "matching_algorithm": 1,
            "parent": 2,
        },
    ]


async def test_select_options_keep_the_ids_they_are_given(make_client: ClientFactory) -> None:
    server = Recording(
        updated("custom_field", data_type="select", extra_data={"select_options": OPTIONS})
    )
    values = TaxonomyValues(
        select_options=(OptionInput(id="aaaa", label="alpha"), OptionInput(label="gamma"))
    )

    _ = await PaperlessTaxonomyWriter(make_client(server)).update_taxonomy_item(
        "custom_field", 7, values
    )

    assert server.sent == [
        (
            "PATCH",
            "/api/custom_fields/7/",
            {
                "extra_data": {
                    "select_options": [{"id": "aaaa", "label": "alpha"}, {"label": "gamma"}]
                }
            },
            {},
        )
    ]


async def test_renaming_a_select_field_sends_its_options_along(
    make_client: ClientFactory,
) -> None:
    """Paperless 3.4.2 answers 400 to a PATCH of a select field without its options."""
    select = wire(
        "custom_field",
        7,
        "Old",
        data_type="select",
        extra_data={"select_options": OPTIONS},
        document_count=1,
    )
    server = Recording(
        httpx2.Response(200, json=select),
        updated("custom_field", data_type="select", extra_data={"select_options": OPTIONS}),
    )

    _ = await PaperlessTaxonomyWriter(make_client(server)).update_taxonomy_item(
        "custom_field", 7, TaxonomyValues(name="New")
    )

    assert [(method, path) for method, path, _, _ in server.sent] == [
        ("GET", "/api/custom_fields/7/"),
        ("PATCH", "/api/custom_fields/7/"),
    ]
    assert server.sent[1][2] == {
        "name": "New",
        "extra_data": {
            "select_options": [{"id": "aaaa", "label": "alpha"}, {"id": "bbbb", "label": "beta"}]
        },
    }


async def test_renaming_another_custom_field_sends_just_the_name(
    make_client: ClientFactory,
) -> None:
    string = wire("custom_field", 7, "Old")
    server = Recording(httpx2.Response(200, json=string), updated("custom_field"))

    _ = await PaperlessTaxonomyWriter(make_client(server)).update_taxonomy_item(
        "custom_field", 7, TaxonomyValues(name="New")
    )

    assert server.sent[1][2] == {"name": "New"}


async def test_a_pattern_changed_alone_is_sent_with_the_current_algorithm(
    make_client: ClientFactory,
) -> None:
    """Paperless 3.4.2 checks a regular expression only if the request has both fields."""
    current = wire("correspondent", 7, "Old", matching_algorithm=4, match="^a")
    server = Recording(
        httpx2.Response(200, json=current),
        updated("correspondent", matching_algorithm=4, match="^b"),
    )

    _ = await PaperlessTaxonomyWriter(make_client(server)).update_taxonomy_item(
        "correspondent", 7, TaxonomyValues(match="^b")
    )

    assert [(method, path) for method, path, _, _ in server.sent] == [
        ("GET", "/api/correspondents/7/"),
        ("PATCH", "/api/correspondents/7/"),
    ]
    assert server.sent[1][2] == {"match": "^b", "matching_algorithm": 4}


async def test_switching_to_regex_alone_is_sent_with_the_current_pattern(
    make_client: ClientFactory,
) -> None:
    current = wire("tag", 7, "Old", matching_algorithm=1, match="(")
    server = Recording(
        httpx2.Response(200, json=current), updated("tag", matching_algorithm=4, match="(")
    )

    _ = await PaperlessTaxonomyWriter(make_client(server)).update_taxonomy_item(
        "tag", 7, TaxonomyValues(matching_algorithm="regex")
    )

    assert server.sent[1][2] == {"matching_algorithm": 4, "match": "("}


async def test_another_algorithm_alone_and_a_complete_pair_need_no_read(
    make_client: ClientFactory,
) -> None:
    server = Recording(updated("tag"), updated("tag"))
    writer = PaperlessTaxonomyWriter(make_client(server))

    _ = await writer.update_taxonomy_item("tag", 7, TaxonomyValues(matching_algorithm="all"))
    _ = await writer.update_taxonomy_item(
        "tag", 7, TaxonomyValues(matching_algorithm="regex", match="x")
    )

    assert [method for method, _, _, _ in server.sent] == ["PATCH", "PATCH"]


@pytest.mark.parametrize("kind", ["tag", "correspondent", "document_type", "storage_path"])
async def test_a_delete_is_a_delete_of_that_object(
    make_client: ClientFactory, kind: WritableKind
) -> None:
    server = Recording(httpx2.Response(204))

    await PaperlessTaxonomyWriter(make_client(server)).delete_taxonomy_item(kind, 7)

    assert server.sent == [("DELETE", f"{PATHS[kind]}7/", None, {})]


async def test_a_custom_field_is_deleted_too(make_client: ClientFactory) -> None:
    server = Recording(httpx2.Response(204))

    await PaperlessTaxonomyWriter(make_client(server)).delete_taxonomy_item("custom_field", 7)

    assert server.sent[0][:2] == ("DELETE", "/api/custom_fields/7/")


@pytest.mark.parametrize(
    ("status", "body", "error", "message"),
    [
        (400, {"parent": ["Cannot set parent to a descendant."]}, Validation, "parent: Cannot"),
        (400, {"path": ["Invalid variable detected."]}, Validation, "path: Invalid variable"),
        (403, {"detail": "You do not have permission."}, PermissionDenied, "may not perform"),
        (404, {"detail": "No Tag matches the given query."}, NotFound, "does not exist"),
    ],
)
async def test_paperless_errors_become_domain_errors(
    make_client: ClientFactory, status: int, body: Json, error: type[Exception], message: str
) -> None:
    writer = PaperlessTaxonomyWriter(make_client(Recording(httpx2.Response(status, json=body))))

    with pytest.raises(error, match=message):
        _ = await writer.update_taxonomy_item("tag", 7, TaxonomyValues(name="x"))
    with pytest.raises(error, match=message):
        _ = await PaperlessTaxonomyWriter(
            make_client(Recording(httpx2.Response(status, json=body)))
        ).create_taxonomy_item("tag", "x", TaxonomyValues())
    with pytest.raises(error, match=message):
        await PaperlessTaxonomyWriter(
            make_client(Recording(httpx2.Response(status, json=body)))
        ).delete_taxonomy_item("tag", 7)


async def test_an_unknown_matching_algorithm_in_an_answer_is_an_unexpected_response(
    make_client: ClientFactory,
) -> None:
    server = Recording(updated("correspondent", matching_algorithm=9))

    with pytest.raises(PaperlessMcpError, match="unexpected response"):
        _ = await PaperlessTaxonomyWriter(make_client(server)).update_taxonomy_item(
            "correspondent", 7, TaxonomyValues(name="x")
        )


async def test_a_tag_answered_without_a_matching_rule_has_none(make_client: ClientFactory) -> None:
    row = wire("tag", 7, "New", document_count=1)
    for name in ("matching_algorithm", "match", "is_insensitive"):
        del row[name]
    server = Recording(httpx2.Response(200, json=row))

    item = await PaperlessTaxonomyWriter(make_client(server)).update_taxonomy_item(
        "tag", 7, TaxonomyValues(colour="#000000")
    )

    assert (item.matching_algorithm, item.match, item.case_insensitive) == (None, None, None)
    assert item.is_inbox_tag is False


@pytest.mark.parametrize(
    ("kind", "stray"),
    [
        ("correspondent", {"is_inbox_tag": True, "path": "x/{{ title }}"}),
        ("custom_field", {"match": "m", "matching_algorithm": 3, "is_insensitive": False}),
        ("tag", {"path": "x/{{ title }}"}),
        ("storage_path", {"is_inbox_tag": True}),
    ],
)
async def test_settings_of_other_kinds_in_an_answer_are_left_out(
    make_client: ClientFactory, kind: WritableKind, stray: Json
) -> None:
    server = Recording(updated(kind, **stray))

    values = (
        TaxonomyValues(name="x", select_options=(OptionInput(label="a"),))
        if kind == "custom_field"
        else TaxonomyValues(name="x", matching_algorithm="any")
    )

    item = await PaperlessTaxonomyWriter(make_client(server)).update_taxonomy_item(kind, 7, values)

    leaked = {
        "is_inbox_tag": item.is_inbox_tag if kind != "tag" else None,
        "path": item.path if kind != "storage_path" else None,
        "match": item.match if kind == "custom_field" else None,
        "algorithm": item.matching_algorithm if kind == "custom_field" else None,
        "case": item.case_insensitive if kind == "custom_field" else None,
    }
    assert leaked == dict.fromkeys(leaked)


async def test_renaming_a_field_with_other_extra_data_does_not_invent_options(
    make_client: ClientFactory,
) -> None:
    monetary = wire(
        "custom_field", 7, "Old", data_type="monetary", extra_data={"default_currency": "EUR"}
    )
    server = Recording(httpx2.Response(200, json=monetary), updated("custom_field"))

    _ = await PaperlessTaxonomyWriter(make_client(server)).update_taxonomy_item(
        "custom_field", 7, TaxonomyValues(name="New")
    )

    assert server.sent[1][2] == {"name": "New"}


async def test_switching_to_regex_with_no_pattern_stored_sends_an_empty_one(
    make_client: ClientFactory,
) -> None:
    current = wire("tag", 7, "Old", matching_algorithm=1)
    del current["match"]
    server = Recording(httpx2.Response(200, json=current), updated("tag", matching_algorithm=4))

    _ = await PaperlessTaxonomyWriter(make_client(server)).update_taxonomy_item(
        "tag", 7, TaxonomyValues(matching_algorithm="regex")
    )

    assert server.sent[1][2] == {"matching_algorithm": 4, "match": ""}
