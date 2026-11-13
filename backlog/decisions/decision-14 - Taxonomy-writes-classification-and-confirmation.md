---
id: decision-14
title: 'Taxonomy writes: classification and confirmation'
date: '2026-11-12T22:03:00Z'
status: accepted
---
## Context

PLMCP-19 adds `create_taxonomy_item`, `update_taxonomy_item` and `delete_taxonomy_item` for tags, correspondents, document types, storage paths and custom fields. Probed on Paperless 3.4.2:

- Deleting any of them is final (taxonomy has no trash). Documents stay but lose the reference or the custom field value; deleting a tag deletes its child tags at any depth too.
- A select field's options carry server-made ids that documents store. A change that sends options without their ids gives every option a new id, so all documents silently lose their value. Removing an option that documents hold leaves the stale id on them; `update_document` cannot set it again.
- Paperless accepts a change of a custom field's data type under existing values, keeps duplicate option ids and labels, and answers 500 to malformed options.
- Deleting or editing a storage path did not rename the stored files of its documents on the 3.4.2 E2E stack (their `archived_file_name` stayed, probed after the change), so the tools make no claim about re-filing.
- Deleting a tag also deletes child tags the caller cannot see (a user deleted their own tag and the admin-owned child under it went too).
- A PATCH of a select field without its options is a 400; a regular expression is checked only if the request carries both the match and the algorithm.

## Decision

- One tool per action with a `kind` (tag, correspondent, document_type, storage_path, custom_field) and flat, kind-specific settings validated per kind by the service: three tools instead of fifteen keep the default profile within its budget, and the annotations differ per action, which read-only mode relies on.
- `create_taxonomy_item` and `update_taxonomy_item` are not destructive (`destructive_hint: false`; update is idempotent). They need no confirmation, except that **removing select options** needs `confirm=true`, like the owner change of `update_document` (decision-12): without it the call fails before anything is written with a client-safe error that explains the effect and counts the documents the caller can see that hold a removed option.
- `delete_taxonomy_item` is destructive and takes `confirm: bool = False`. Without `confirm` it deletes nothing and returns what would happen (documents affected, the visible child tags that go with a tag with their document counts, and that child tags the caller cannot see go too) and how to confirm (decision-6).
- Select options are changed one by one (add, rename, remove); the service always sends the whole list with the ids of the options that stay. The data type of a custom field is never changed by a tool; to change it, create a new field.
- Writes act on the current state read right before, not on the resolver's cache, and invalidate the resolver's rows of the kind for every credential afterwards, also when the write failed.
- Objects are created owned by the user and matching nothing (`matching_algorithm` none) unless asked, as `create_missing_tags` does for tags. Sharing and permissions are `bulk_edit_taxonomy` (PLMCP-22).
- This supplements decision-6 and decision-12; it changes none of their rules.

## Consequences

- Deleting takes two calls, and so does dropping a select option; adding options, renaming options and every other edit take one.
- A tag delete cascades; the preview names the child tags, so the caller knows before confirming.
- Counts in previews and confirm errors cover the documents the caller may see; child tags the caller cannot see are neither listed nor counted but are deleted too, which the preview says.
- An update or delete reads the object right before it writes (the same read-then-write race as `update_document`): a concurrent change in between is overwritten, and Paperless offers no conditional update.
- Built before PLMCP-17 (which owns the shared confirm/preview mechanism) had merged; the delete confirmation is kept in `TaxonomyEditor.delete` so it can adopt that mechanism, and this decision is then to be adjusted to build on it.
- Two Paperless quirks live in the adapter, not in callers: the options of a select field and the matching pair are read and sent back when an update leaves them out.

## Enforcement

- Service tests (tests/services/test_taxonomy_edits.py): option removal fails without `confirm` and writes nothing, add and rename need none and keep every id, a delete without `confirm` writes nothing and names the effect, the resolver cache is dropped after every write also a failed one.
- Contract tests on the fake and live 3.4.2 (tests/contract/test_taxonomy_edits.py): option ids, the stale value after a removal, the tag cascade, cycles, the pattern check on a partial update.
- Registry tests (tests/tools/test_registry.py) require `confirm` defaulting to false on the destructive `delete_taxonomy_item` and keep the annotations in the central table; the surface snapshot shows the three tools; the E2E suite (tests/e2e/test_taxonomy_edits.py) runs them through the assembled server.
