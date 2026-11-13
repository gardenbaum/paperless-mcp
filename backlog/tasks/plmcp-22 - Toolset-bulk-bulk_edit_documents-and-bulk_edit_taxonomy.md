---
id: PLMCP-22
title: 'Toolset bulk: bulk_edit_documents and bulk_edit_taxonomy'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-09T19:26:00Z'
updated_date: '2026-11-13T02:51:00Z'
labels: []
milestone: m-3
dependencies:
  - PLMCP-16
  - PLMCP-17
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 22000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Re-filing many documents at once is a common need. One wrong bulk call can damage hundreds of documents, so every bulk operation can be previewed first (design §5.4, §6).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 bulk_edit_documents offers typed operations (tags add/remove/modify, correspondent, document type, storage path, custom fields, delete, reprocess) instead of an untyped parameter dict
- [ ] #2 dry_run returns the affected count and a sample without changing anything; execution requires confirm
- [ ] #3 bulk_edit_taxonomy supports delete and permissions for taxonomy items, with confirm
- [ ] #4 Custom-field bulk changes keep other fields (merge semantics as in update_document)
- [ ] #5 Adapter tests are schema-guarded and E2E covers the main flow on seeded data
- [ ] #6 FakePaperless and the contract suite cover every new port method
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Port: new BulkWriter in domain/ports.py (bulk_edit_documents(ids, change), delete_taxonomy_items(kind, ids), set_taxonomy_permissions(kind, ids, change)); id-level change models in domain/bulk.py. DocumentQuery gets ids (id__in) so one search returns the current state of up to 100 explicitly selected documents (preview, diff, missing/hidden ids).
2. Adapter (paperless/bulk.py): POST /api/documents/bulk_edit/ (modify_tags, set_correspondent/document_type/storage_path, modify_custom_fields dict form only, delete, reprocess) and POST /api/bulk_edit_objects/ (delete, set_permissions); 403 'Insufficient permissions' mapped to an all-or-nothing message; never retried. Schema-guarded adapter tests first (red).
3. FakePaperless (write-guarded) + contract tests on fake and live 3.4.2 for every method (own documents/taxonomy, cleanup).
4. Services: BulkDocumentEditor (typed ops discriminated union, ids only, cap 100, tag hierarchy via tag_hierarchy, custom field merge via custom_values, diff against current state, dry_run preview, confirm via ConfirmationRequired, cache invalidation) and BulkTaxonomyEditor (delete, set_permissions with names/ids of users and groups).
5. Tools: new toolset bulk (opt-in) in tools/bulk.py: bulk_edit_documents, bulk_edit_taxonomy; ANNOTATIONS (destructive, confirm), TOOL_TOOLSETS, TOOLSETS, Services, server wiring; registry/surface snapshot (deliberate), selection tests.
6. E2E through the assembled server on 3.4.2 (own documents/taxonomy). Gates: just check, full just e2e, just mutation (killable mutants get tests, decision-13).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Live-verified on the 3.4.2 E2E stack (probes with raw API calls plus the Paperless source in the image): (1) POST /api/documents/bulk_edit/ answers {"result":"OK"} synchronously for tag/reference/custom-field/delete changes (visible on return); reprocess answers OK too but only queues a reprocess_document task whose id is NOT returned, so no TaskService wait is possible or needed. (2) The whole request is validated first: unknown, repeated or trashed document ids -> 400 'Some documents in documents don't exist or were specified twice' (an empty list is accepted as a no-op, so the service refuses it); unknown tag/correspondent/type/path/field -> 400; wrong custom field value -> 400 with the field named; then permissions -> plain-text 403 'Insufficient permissions' for the whole batch (global change_document + change permission on every document, hidden ones indistinguishable; delete also needs ownership and delete_document). A refused batch changes nothing. (3) modify_tags removes (with all descendants) and then adds (with all ancestors): adding a child brings its parents, removing a parent drops its children, a tag in both lists ends up added. (4) modify_custom_fields dict form {field_id: value} merges: other fields stay, same field is overwritten; the LIST form [ids] resets an existing field's value to null, so the adapter only ever sends the dict form; removing a field the document lacks is fine; document links are mirrored; a document whose own id is in the link list is skipped for that field. (5) POST /api/bulk_edit_objects/: object_type tags|correspondents|document_types|storage_paths only (custom fields are not accepted); empty list 400; unknown/repeated ids, users, groups 400; 403 unless global permission and owner/unowned; delete of a tag cascades to descendants; set_permissions without merge replaces the grants, with merge adds; the owner is applied when the key is present (null removes it) but with merge only objects WITHOUT an owner get it and null never removes one (so the tool refuses owner/clear_owner together with merge). Decisions: documents are selected by explicit ids only (cap 100 = largest search page; a query-based selection would need an unbounded read) and the number is said in the schema and the error; the preview/diff reads the selected documents with ONE search_documents call using a new DocumentQuery.ids (id__in), so a missing or hidden id is a NotFound before anything is written; documents that already are as asked are counted and not sent; tag ops are three typed operations (add_tags, remove_tags, modify_tags) that all map to Paperless modify_tags; custom fields use values/remove; dry_run returns the typed DocumentBulkReport/TaxonomyBulkReport (decision-15), unconfirmed execution is a ConfirmationRequired tool error; no new decision was needed (decisions 6, 12, 14, 15 are supplemented, none changed).

Red->green per level (honest): adapter tests/adapter/test_bulk.py written first, run red (ModuleNotFoundError paperless.bulk), then green; the schema guard rejected the plain-text 403 'Insufficient permissions' of both endpoints as not a DRF body, so two documented WRONG_RESPONSE_SCHEMAS entries were added (like the existing status 403 one; a deviation list, no gate weakened). DocumentQuery.ids: adapter and contract tests red first (silently ignored field), then green. Contract tests/contract/test_bulk.py: red against the unchanged fake (AttributeError), green on the fake after implementing it; then run live on 3.4.2: first run 1 failure (Paperless words an unknown tag 'Some tags in add_tags don't exist or were specified twice', not 'Tag does not exist': fake message and test regex fixed), second run 1 failure (granting change also grants view: set_permissions_for_objects in the 3.4.2 source; fake fixed, expectation fixed, schema and tool text say 'a change grant includes view'), third run all 90 live contract tests of test_bulk.py and test_document_reader.py green. Service tests (tests/services/test_bulk_documents.py, test_bulk_taxonomy.py) written first, red (ModuleNotFoundError), green after the services; several message assertions were adjusted to the final wording while implementing (e.g. plural 'No documents 2, 999 exist'). Tool tests tests/tools/test_bulk.py red (unknown toolset bulk), green after tools/bulk.py; FastMCP drops the discriminator and inlines the union as oneOf with an op const (asserted). Mutation: first run 98.9 % with 57 killable survivors in the new modules (hints and plural wording untested, merge flag not asserted, child-tag order, list limit of the titles, cache kind for custom fields, ReprocessChange, one aggregation key): each got a test; second run 99.9 % and 3 survivors judged equivalent after mutmut show (httpx2 upper-cases the method, 2 entries; _document_ids isinstance only for the type checker) and listed in mutation-equivalents.toml with reasons: score 100.0 %. Deliberate snapshot change: tests/tools/__snapshots__/test_surface.ambr, profile 'all' only (+2 tools, 713 added lines, nothing removed); 'default' and 'read-only' unchanged (bulk tools are opt-in and not read-only). tests/tools/test_visibility.py: its synthetic extra toolset was named 'bulk' (a placeholder for this task); renamed to 'pdf' so the shipped bulk toolset does not collide. test_coverage.py: the Ports map gets bulk_writes. No gate, threshold or decision changed.

Gates on the final tree: just check exit 0 (1612 tests, branch coverage over 99 %, diff coverage 100 % on the changed lines, import contracts kept); just mutation exit 0, score 100.0 % after 3 reviewed equivalents (99.9 % before them); full just e2e: first run 342 passed + 1 failed (tests/e2e/test_bulk.py: my restricted-user case used an admin-owned tag the reader cannot see, a test bug, fixed), second run 343 passed, 6 skipped (AI), 0 failed. The E2E tests of this task (tests/e2e/test_bulk.py, 7) create their own documents and taxonomy and delete them, trash entries included; a run of mine killed by a shell timeout left two plmcp22- correspondents, deleted by hand. Also emptied from the trash by hand: 8 contract-* documents of earlier runs (deleted 2026-11-12 23:16, not from this task). Not touched: older plmcp16/plmcp19/e2e-task entries that other tests left in the trash. Open risks: the preview cannot tell whether the user may change every document (a search hit carries no change permission), so a user without that permission sees a preview and then a refusal that changes nothing; a concurrent change between the read and the request is merged by Paperless for tags and fields (documented in the service docstring); a bulk reprocess returns no task id, so there is nothing to follow; custom fields are not supported by bulk_edit_taxonomy because Paperless's object endpoint does not take them; Paperless group grants are stored by the fake but group membership is not modelled, group effects are proven live only through the contract test grants reader and the E2E test.
<!-- SECTION:NOTES:END -->
