from paperless_mcp.domain.errors import (
    CONFIRM_HINT,
    ConfirmationRequired,
    NotFound,
    Validation,
    quoted,
)


def test_the_description_is_the_message_followed_by_the_hint() -> None:
    error = NotFound("No such document.", hint="Search with search_documents.")

    assert error.describe() == "No such document. Hint: Search with search_documents."


def test_without_a_hint_the_description_is_the_message() -> None:
    assert NotFound("No such document.").describe() == "No such document."


def test_a_missing_confirmation_is_a_validation_error_that_says_nothing_was_changed() -> None:
    error = ConfirmationRequired.for_effect("move document 5 to the trash")

    assert isinstance(error, Validation)
    assert error.message == "Nothing was changed. Confirming would move document 5 to the trash."
    assert (
        error.hint
        == CONFIRM_HINT
        == "Call the tool again with the same arguments and confirm=true."
    )


def test_a_confirmation_error_can_carry_its_own_message_and_hint() -> None:
    error = ConfirmationRequired("Own text.", hint="Own hint.")

    assert error.describe() == "Own text. Hint: Own hint."


def test_a_confirmation_error_without_a_hint_names_confirm_true() -> None:
    assert ConfirmationRequired("Own text.").hint == CONFIRM_HINT


def test_quoted_text_is_one_line_in_single_quotes() -> None:
    assert quoted("two\nlines\t here") == "'two lines here'"


def test_quoted_text_is_cut_at_the_limit_with_an_ellipsis() -> None:
    assert quoted("abcdefghij", 5) == "'abcd…'"
    assert quoted("abcde", 5) == "'abcde'"
