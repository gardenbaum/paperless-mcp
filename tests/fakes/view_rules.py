"""What Paperless 3.4.2 accepts in a saved view write, as pure checks for FakePaperless.

Probed on the live stack (PLMCP-25); the contract suite keeps the fake and Paperless in step. A
failed check raises `Validation` with the message of the 400.
"""

from typing import TYPE_CHECKING

from fakes.document_values import reject
from paperless_mcp.domain.views import RULE_TYPES

if TYPE_CHECKING:
    from collections.abc import Sequence

    from paperless_mcp.domain.views import FilterRule

MAX_VALUE = 255


def check_rules(rules: Sequence[FilterRule]) -> None:
    """Every rule type is one of the 3.4.2 types and every value at most 255 characters."""
    for position, rule in enumerate(rules):
        if rule.rule_type not in RULE_TYPES:
            reject(
                f"filter_rules.{position}.rule_type", f'"{rule.rule_type}" is not a valid choice.'
            )
        if rule.value is not None and len(rule.value) > MAX_VALUE:
            reject(
                f"filter_rules.{position}.value",
                f"Ensure this field has no more than {MAX_VALUE} characters.",
            )


def check_page_size(page_size: int | None) -> None:
    """A page size is at least 1."""
    if page_size is not None and page_size < 1:
        reject("page_size", "Ensure this value is greater than or equal to 1.")
