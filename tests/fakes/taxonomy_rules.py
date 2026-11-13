"""What Paperless 3.4.2 accepts in taxonomy writes, as pure checks for FakePaperless.

Each rule was probed on the live stack (PLMCP-19); the contract suite (tests/contract) keeps the
fake and Paperless in step. A failed check raises `Validation` with the message of the 400.
"""

import re
import secrets
import string
from typing import cast

from fakes.document_values import reject
from paperless_mcp.domain.taxonomy import MatchingAlgorithm, OptionInput, SelectOption

MAX_NAME = 128
MAX_MATCH = 256
# The variables a storage path template may use (advanced_usage.md "Filename templates").
PATH_VARIABLES = frozenset(
    {
        *("asn", "correspondent", "document_type", "tag_list", "title", "created", "added"),
        *("created_year", "created_year_short", "created_month", "created_month_name"),
        *("created_month_name_short", "created_day", "added_year", "added_year_short"),
        *("added_month", "added_month_name", "added_month_name_short", "added_day"),
        *("owner_username", "original_name", "doc_pk", "tag_name_list", "custom_fields"),
        *("document",),
    }
)
_EXPRESSION = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
_COLOUR = re.compile(r"#[0-9a-fA-F]{6}")
_ID_LENGTH = 16


def check_name(name: str) -> str:
    """The name as Paperless stores it: trimmed, not blank, at most 128 characters."""
    name = name.strip()
    if not name:
        reject("name", "This field may not be blank.")
    if len(name) > MAX_NAME:
        reject("name", f"Ensure this field has no more than {MAX_NAME} characters.")
    return name


def check_colour(colour: str) -> None:
    """A colour is `#` and six hexadecimal digits."""
    if not _COLOUR.fullmatch(colour):
        reject("color", "Invalid color.")


def check_match(algorithm: MatchingAlgorithm, match: str) -> None:
    """The match is at most 256 characters, and a regular expression must compile."""
    if len(match) > MAX_MATCH:
        reject("match", f"Ensure this field has no more than {MAX_MATCH} characters.")
    if algorithm == "regex":
        try:
            _ = re.compile(match)
        except re.error:
            reject("match", "Invalid regular expression, see log for details.")


def check_path(path: str) -> None:
    """A template is not blank, has no `..`, and uses only known variables in `{{ }}`."""
    if not path.strip():
        reject("path", "This field may not be blank.")
    expressions = cast("list[str]", _EXPRESSION.findall(path))
    names = [re.match(r"\s*([A-Za-z_]\w*)", found) for found in expressions]
    stripped = _EXPRESSION.sub("", path)
    unknown = any(m is None or m.group(1) not in PATH_VARIABLES for m in names)
    if unknown or ".." in path or "{{" in stripped or "}}" in stripped:
        reject("path", "Invalid variable detected.")


def with_ids(options: tuple[OptionInput, ...]) -> tuple[SelectOption, ...]:
    """The options as stored: blank labels are refused, options without an id get a new one."""
    stored: list[SelectOption] = []
    for option in options:
        if not option.label.strip():
            reject("error", "extra_data.select_options must be a valid list")
        number = option.id or "".join(
            secrets.choice(string.ascii_letters + string.digits) for _ in range(_ID_LENGTH)
        )
        stored.append(SelectOption(id=number, label=option.label))
    return tuple(stored)
