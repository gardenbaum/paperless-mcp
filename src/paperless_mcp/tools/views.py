"""The views toolset: saved views (named document filters) and running them.

Paperless stores a view as numbered filter rules and leaves running it to its web UI;
``run_saved_view`` translates the rules into the document query the search tool uses, so a view
returns the rows and paging of ``search_documents``. The toolset is opt-in.
"""

from typing import Annotated

from fastmcp import Context
from fastmcp.server.providers import LocalProvider
from pydantic import Field

from paperless_mcp.domain.documents import DocumentSearchResult, NameOrId
from paperless_mcp.domain.views import (
    RuleInput,
    SavedViewInfo,
    SavedViewListing,
    ViewCreate,
    ViewDeletion,
    ViewEdit,
)
from paperless_mcp.tools._shared import ANNOTATIONS, Confirm, boundary, services

TOOLSET = "views"
PAGE_SIZE = 25
LIST_LIMIT = 100

provider = LocalProvider()

type View = Annotated[NameOrId, Field(description="The saved view: its name or id.")]
type Rules = Annotated[
    list[RuleInput] | None,
    Field(description="Filter rules, all of which must match; see the tool description."),
]
type SortField = Annotated[
    str | None,
    Field(
        description=(
            "Sort by created, added, modified, title, archive_serial_number, correspondent__name, "
            "document_type__name, owner, num_notes, page_count or custom_field_<id>."
        )
    ),
]
type PageSize = Annotated[
    int | None, Field(ge=1, description="The page size the Paperless web UI uses for the view.")
]

RULE_REFERENCE = """\
Rule types, by label or number (a taxonomy value is a name or an id, one rule per value):
- Tags: has tag 6 (all of them), has tags in 22 (at least one), does not have tag 17, has any tag
  7 (true: tagged, false: untagged).
- Correspondent is 3, document type is 4, storage path is 25: a name or id, no value for none
  assigned, -1 for any assigned. Has correspondent in 26, does not have correspondent in 27, and
  the same for document types (28, 29) and storage paths (30, 31).
- Owner is 32, has owner in 33, does not have owner in 35 (a user name or id), does not have
  owner 34 (true), is shared by me 37 (your user id).
- Searches, at most one of them except that a title or text search may come with a fulltext
  query: title contains 0, simple title search 48 (title), title or content contains 19, simple
  text search 49 (title and content), fulltext query 20 (the query syntax of search_documents),
  more like this 21 (a document id).
- Other text: content contains 1, mime type is 47 (application/pdf), custom field value contains
  36.
- Numbers: ASN is 2, ASN greater than 23, ASN less than 24, does not have ASN 18 (true), created
  year 10, created month 11, created day 12.
- Dates (YYYY-MM-DD): created before 8, created after 9, created from 44, created to 43 (from and
  to include the day), added before 13, added after 14, added from 46, added to 45, modified
  before 15, modified after 16.
- Flags (true or false): is in inbox 5, has duplicates 50, does not have custom field 41 (true:
  has some custom field, false: none; the label is Paperless's).
- Custom fields (names or ids): has custom fields 38 (all of them), has custom field in 39 (at
  least one), does not have custom field in 40, custom fields query 42 (a custom_field_query as
  JSON, as in search_documents).
Except for tags, 'in' and 'not in' types and custom fields, a view may hold each type once.
"""

CREATE_DESCRIPTION = (
    """Create a saved view: a named document filter that run_saved_view can run.

rules is a list of {rule, value}; a document must match all of them. Names in values are looked
up like in search_documents. The rules are checked as run_saved_view would read them, and the
call fails, naming the rule type, if one cannot be run. A view without rules matches every
document. The view belongs to the user. Returns it as stored.

"""
    + RULE_REFERENCE
)
UPDATE_DESCRIPTION = (
    """Change a saved view; only the settings you name change.

rules replaces all the rules of the view (pass the whole new list; an empty list removes them),
in the form of create_saved_view. The change is checked as run_saved_view would read it. Returns
the view as stored.

"""
    + RULE_REFERENCE
)


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["list_saved_views"])
@boundary
async def list_saved_views(ctx: Context) -> SavedViewListing:
    """List the saved views the current user may see, with their filter rules.

    A saved view is a named document filter with a sort order, kept in Paperless. Each rule shows
    its type number, its label and its value (usually an id: see list_taxonomy to look names up).
    Run a view with run_saved_view. Needs the view permission for saved views in Paperless.
    """
    return await services(ctx).views.list()


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["get_saved_view"])
@boundary
async def get_saved_view(ctx: Context, view: View) -> SavedViewInfo:
    """Read one saved view with its filter rules, sort order and page size.

    The view matches by exact name, then ignoring case and accents, or by id; an unknown or
    ambiguous name is an error that lists the closest names or candidates.
    """
    return await services(ctx).views.get(view)


@provider.tool(
    tags={TOOLSET}, annotations=ANNOTATIONS["create_saved_view"], description=CREATE_DESCRIPTION
)
@boundary
async def create_saved_view(  # noqa: PLR0913 -- one parameter per setting of a view
    ctx: Context,
    *,
    name: Annotated[str, Field(min_length=1, max_length=128, description="A name not yet taken.")],
    rules: Rules = None,
    sort_field: SortField = None,
    sort_reverse: Annotated[bool, Field(description="Sort descending.")] = False,
    page_size: PageSize = None,
) -> SavedViewInfo:
    """Create a saved view (the description is CREATE_DESCRIPTION)."""
    spec = ViewCreate(
        name=name,
        rules=tuple(rules or ()),
        sort_field=sort_field,
        sort_reverse=sort_reverse,
        page_size=page_size,
    )
    return await services(ctx).views.create(spec)


@provider.tool(
    tags={TOOLSET}, annotations=ANNOTATIONS["update_saved_view"], description=UPDATE_DESCRIPTION
)
@boundary
async def update_saved_view(  # noqa: PLR0913 -- one parameter per setting of a view
    ctx: Context,
    view: View,
    *,
    name: Annotated[str | None, Field(description="A new name, not yet taken.")] = None,
    rules: Rules = None,
    sort_field: SortField = None,
    sort_reverse: Annotated[bool | None, Field(description="Sort descending or not.")] = None,
    page_size: PageSize = None,
    clear_sort: Annotated[bool, Field(description="Remove the sort field.")] = False,
    clear_page_size: Annotated[bool, Field(description="Remove the page size.")] = False,
) -> SavedViewInfo:
    """Change a saved view (the description is UPDATE_DESCRIPTION)."""
    edit = ViewEdit(
        name=name,
        rules=None if rules is None else tuple(rules),
        sort_field=sort_field,
        sort_reverse=sort_reverse,
        page_size=page_size,
        clear_sort=clear_sort,
        clear_page_size=clear_page_size,
    )
    return await services(ctx).views.update(view, edit)


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["delete_saved_view"])
@boundary
async def delete_saved_view(ctx: Context, view: View, *, confirm: Confirm = False) -> ViewDeletion:
    """Delete a saved view for good: it has no trash. No document is touched.

    Without confirm it deletes nothing and fails with an error that says the effect (the rules
    and sort order that are lost); describe that to the user and call again with confirm=true once
    they agree.
    """
    return await services(ctx).views.delete(view, confirm=confirm)


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["run_saved_view"])
@boundary
async def run_saved_view(
    ctx: Context,
    view: View,
    page: Annotated[int, Field(ge=1, description="Page number, from 1.")] = 1,
    page_size: Annotated[
        int, Field(ge=1, le=LIST_LIMIT, description="Documents per page, at most 100.")
    ] = PAGE_SIZE,
) -> DocumentSearchResult:
    """Find the documents of a saved view, as the Paperless web UI lists them.

    The filter rules of the view become the same filters search_documents takes, and the view's
    sort order applies; the rows and paging are those of search_documents. A rule that cannot be
    run (an unknown type, a value that does not fit) is an error naming the rule type; fix the
    view with update_saved_view. count is the total; pass next_page as page to see more. The
    page size of the view is a setting of the web UI and is not used.
    """
    return await services(ctx).views.run(view, page=page, page_size=page_size)
