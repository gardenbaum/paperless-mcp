from paperless_mcp.domain.errors import NotFound


def test_the_description_is_the_message_followed_by_the_hint() -> None:
    error = NotFound("No such document.", hint="Search with search_documents.")

    assert error.describe() == "No such document. Hint: Search with search_documents."


def test_without_a_hint_the_description_is_the_message() -> None:
    assert NotFound("No such document.").describe() == "No such document."
