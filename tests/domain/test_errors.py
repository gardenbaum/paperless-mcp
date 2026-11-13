import pytest

from paperless_mcp.domain.errors import (
    CONFIRM_HINT,
    ConfirmationRequired,
    NotFound,
    Validation,
    listed,
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
    assert error.hint == CONFIRM_HINT


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


@pytest.mark.decision("decision-15")
def test_the_hint_tells_the_model_to_ask_the_user_before_confirming() -> None:
    assert CONFIRM_HINT == (
        "Describe the effect to the user and call the tool again with the same arguments and "
        "confirm=true only once they agree, or if they already asked for exactly this."
    )


@pytest.mark.decision("decision-15")
def test_a_refusal_carries_text_only() -> None:
    assert vars(ConfirmationRequired.for_effect("x")).keys() == {"message", "hint"}


def test_quoted_text_uses_double_quotes_when_it_holds_a_single_quote() -> None:
    assert quoted("Bob's invoice") == '"Bob\'s invoice"'


def test_quoted_text_with_both_quotes_swaps_the_single_ones() -> None:
    assert quoted('Bob\'s "best" invoice') == "'Bob\u2019s \"best\" invoice'"


def test_a_list_shows_the_names_up_to_the_cap_and_counts_the_rest() -> None:
    names = ["a", "b", "c", "d", "e", "f", "g"]

    assert listed(names, limit=3) == "'a', 'b', 'c' and 4 more"
    assert listed(names[:3], limit=3) == "'a', 'b', 'c'"
    assert listed(names[:1], limit=3) == "'a'"
    assert listed([], limit=3) == "none"
    assert listed(names[:4], limit=3) == "'a', 'b', 'c' and 1 more"
