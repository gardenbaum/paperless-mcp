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
- `create_taxonomy_item` and `update_taxonomy_item` are not destructive (`destructive_hint: false`; update is idempotent). They need no confirmation, except that **removing select options** needs `confirm=true`, like the owner change of `update_document` (decision-12).
- `delete_taxonomy_item` is destructive and takes the shared `Confirm` parameter (default false).
- Both confirmations use the mechanism of decision-15: the service raises `ConfirmationRequired` before any write, and the tool result is an error. Nothing taxonomy-specific about its form; what is specific is the effect it names. For a delete: the object, the documents that lose it (visible to the caller), for a tag the visible child tags with their document counts, and that child tags the caller cannot see are deleted too. For an option removal: the removed options and how many visible documents hold one of them. A confirmed delete returns a typed `TaxonomyDeletion` (what was deleted, counts, deleted child tags).
- Select options are changed one by one (add, rename, remove); the service always sends the whole list with the ids of the options that stay. The data type of a custom field is never changed by a tool; to change it, create a new field.
- Writes act on the current state read right before, not on the resolver's cache, and invalidate the resolver's rows of the kind for every credential afterwards, also when the write failed.
- Objects are created owned by the user and matching nothing (`matching_algorithm` none) unless asked, as `create_missing_tags` does for tags. Sharing and permissions are `bulk_edit_taxonomy` (PLMCP-22).
- This supplements decisions 6, 12 and 15; it changes none of their rules.

## Consequences

- Deleting takes two calls, and so does dropping a select option; adding options, renaming options and every other edit take one.
- A tag delete cascades; the refusal names the visible child tags, so the caller knows before confirming.
- Counts in refusals cover the documents the caller may see; child tags the caller cannot see are neither listed nor counted but are deleted too, which the refusal says.
- An update or delete reads the object right before it writes (the same read-then-write race as `update_document`): a concurrent change in between is overwritten, and Paperless offers no conditional update.
- Two Paperless quirks live in the adapter, not in callers: the options of a select field and the matching pair are read and sent back when an update leaves them out.

## Enforcement

- Service tests (tests/services/test_taxonomy_edits.py): the refusals of an option removal and of a delete carry the exact effect text and `CONFIRM_HINT`, and the fake is created without `allow_writes` so any write on the unconfirmed path fails the test; add and rename need no confirm and keep every id; the resolver cache is dropped after every write, also a failed one.
- Contract tests on the fake and live 3.4.2 (tests/contract/test_taxonomy_edits.py): option ids, the stale value after a removal, the tag cascade, cycles, the pattern check on a partial update.
- Tool tests (tests/tools/test_taxonomy_edits.py): the unconfirmed delete is an `isError` result naming the effect, and `confirm` is the shared parameter defaulting to false. The registry tests (tests/tools/test_registry.py) keep `confirm: bool = False` on the destructive `delete_taxonomy_item` and the annotations in the central table; the surface snapshot shows the three tools; E2E (tests/e2e/test_taxonomy_edits.py) runs them through the assembled server, including a child tag the caller cannot see.
