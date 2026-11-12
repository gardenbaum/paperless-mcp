---
id: PLMCP-16
title: update_document with merge semantics
status: Done
assignee:
  - '@claude'
created_date: '2026-11-09T19:25:00Z'
updated_date: '2026-11-12T21:00:00Z'
labels: []
milestone: m-2
dependencies:
  - PLMCP-11
  - PLMCP-15
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 16000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
A PATCH of custom_fields replaces the whole list in Paperless; two reference servers silently lose fields this way. Updates must never drop data that the caller did not ask to change (design §5.4, anti-goals).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Updates title, correspondent, document type, storage path (by name or id), created date, archive serial number and owner; tags add, remove or set; custom fields set or remove by name
- [x] #2 Setting one custom field never removes others (tested against the fake and live Paperless)
- [x] #3 Values are validated per custom-field data type before sending (select by label, monetary with currency, document-link ids, dates)
- [x] #4 Unknown tag names fail with NotFound and a hint unless create_missing_tags is set
- [x] #5 Returns the updated document detail and invalidates affected cache entries
- [x] #6 FakePaperless and the contract suite cover every new port method
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Port: DocumentWriter.update_document(id, DocumentPatch) -> DocumentRecord and TaxonomyWriter.create_tag(name) -> TaxonomyItem (for create_missing_tags), both in domain/ports.py. DocumentPatch carries ids only; its model_fields_set says what is sent.
2. Adapter (paperless/documents.py, taxonomy.py): PATCH /api/documents/{id}/?fields=<detail fields> sending only the set fields; POST /api/tags/ with matching_algorithm 0 (no auto-matching). 403/404/400 map to PermissionDenied/NotFound/Validation. Schema-guard tests for the PATCH request.
3. FakePaperless: update_document/create_tag with @write and live-probed PATCH semantics (custom_fields list replaced wholesale, tags de-duplicated, per-type value validation, ASN unique, change_document permission, owner-only object change); contract tests on fake+live (own documents created by the backend fixture, deleted afterwards).
4. Services: services/custom_values.py (pure per-data-type normalisation: select label->option id, monetary with optional currency, document-link ids, ISO dates, strict bool/int/float/url/string) and services/document_edits.py (DocumentEditor): resolve names (decision-5), diff against the fresh current record, merged tags and custom_fields lists, create_missing_tags, no PATCH when nothing differs, TaxonomyResolver.invalidate of the kinds whose document_count changed (all on owner change), result via DocumentService.detail(record). Property tests (hypothesis) for the merge and the value normalisation.
5. Tool update_document in tools/core.py (toolset core; annotations: not read-only, destructive_hint false, idempotent, closed world: it merges and never drops unrequested data, so no confirm flow), ANNOTATIONS/TOOL_TOOLSETS entries, Services.edits, server.py wiring of the writer ports, harness support; deliberate surface snapshot update.
6. E2E through the assembled server on live 3.4.2: own document, set one custom field keeps the others, tags add/remove/set, create_missing_tags, name resolution, errors; cleanup of everything created.
7. Residual race: the merge reads the document right before the PATCH (one round trip); a concurrent change to the same document's tags or custom fields in that window is overwritten. Paperless offers no conditional update; bulk_edit modify_custom_fields merges server-side (verified live) but cannot set the other fields, so it would not make the whole update atomic.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Live-verified on 3.4.2 (probes on own documents, deleted again): PATCH custom_fields replaces the list wholesale ([] clears, a field named twice keeps the last value, value null is accepted); PATCH honours ?fields= (answer without OCR text) although the schema omits the parameter. Value rules: select needs the option id (400 otherwise), integer accepts '12' but rejects 1.5/'abc', boolean accepts 0/1 and a string 'true' gives HTTP 500 (so the service accepts JSON booleans only), date only YYYY-MM-DD, url needs a scheme, string max 128, monetary accepts '12.50', '12', 'EUR12.50', 'EUR-12.50' and rejects lowercase currency, 'EUR 12.50' and 3 decimals, documentlink needs a list of existing unique ids. Tags: duplicates collapse, unknown id 400; tags are hierarchical on PATCH: adding a child adds its parent, taking a parent away takes its children (PATCH [child] on [parent, child] gives []). ASN must be unique and >= 0; title max 128, null rejected; owner null is accepted. bulk_edit modify_custom_fields merges server-side and validates, but cannot set the other fields. POST /api/tags/ answers without document_count, creates the tag owned by the creator (invisible to other users), same name+owner is a 400; matching_algorithm 0 sent so a created tag never auto-matches. Reader (no change_document) gets 403 on any PATCH.
Design: DocumentWriter.update_document(id, DocumentPatch) and TaxonomyWriter.create_tag(name) ports (create_tag is the minimum for create_missing_tags; PLMCP-19 should extend TaxonomyWriter). Merge happens in services/document_edits.py (DocumentEditor): fresh read, names resolved, values normalised (services/custom_values.py), diff, only differing fields sent, nothing sent when already true. Annotations decision: update_document is core, read_only false, destructive_hint FALSE, idempotent true, closed world: it never drops unnamed data, so no confirm flow (design lists confirm only for deletes); overwritten values stay in the audit log. Residual race: read then PATCH is two requests, a concurrent change of the same document's tags/custom fields in between is overwritten (no conditional update in Paperless). Created tags stay if the later PATCH fails. Deliberate gate/snapshot changes: tests/tools/__snapshots__/test_surface.ambr (new tool), test_visibility read-only expectation (update_document hidden), schema_guard deviations (PATCH ?fields=, PATCH 200 response and narrow custom field value request body, POST /api/tags/ 201 without document_count), test_stack private-objects test made robust against other tests' documents.

Review fix round 1 (decision-12 supplements decision-6: any owner change needs confirm=true, other classification changes do not; annotations stay non-destructive).
C1: tags are closed over their ancestors before diffing and sending (services/tag_hierarchy.py, pure helpers reusable by PLMCP-22: ancestors, descendants, close_over_ancestors, blocked_removals). tags_set=[child] sends parent+child, repeated calls are no-ops. A removal whose child tags stay is a Validation naming them (live-verified rule: PATCH [child] on [parent, child] gives []). The result is a DocumentUpdate (DocumentDetail plus typed implicit_tags: parents added for you).
I1 live-verified: document links are mirrored on the other document, unlinking leaves [] there; a caller who cannot change the linked document (hidden or view-only) gets 403 'Insufficient permissions' (probed with a temporary user, deleted). Encoded: tool description says so, linked ids must be visible (NotFound with hint), a refused link says change permission is needed there, FakePaperless mirrors/unmirrors and refuses (403), contract test on fake+live, tests link only documents they create. The seed's private document had stale back-links [1,103,125,135,163] from earlier runs; seed() now reconciles it to [public] and the fake seed has the mirror.
I2: title (128/blank), user_can_change (new detail field, PermissionDenied before anything), numeric owner id (checked against the user list unless the user may not list users), linked ids are checked before any tag is created; an error after tags were created names them. ASN uniqueness is left to Paperless (no ASN filter in the search port) and covered by the 'names created tags' path.
I3: owner change/clear needs confirm=true (decision-12). I4: the owner-change invalidation test now warms the reader's own count (1 -> 0 after the owner moves) so it fails without invalidation (verified by removing the invalidation locally: red).
M1 plain hint for tags_remove names. M2 live probes: integer is int32; url: 201 chars accepted so NO length limit is enforced, 'http:' and 'http://' rejected (netloc or path required), 'a:b' accepted; monetary: at most 10 integer digits, 'EUR12.5' accepted, 'EUR9999999999.99' accepted. M3 link lists compared as sets, titles stripped. M4 race in the tool description and PLMCP-37 note. M5 schema-guard rewrite narrowed to bool and list. M6 fake: one-decimal monetary, int32/url limits, object-level change grants noted as a known gap. M7 description: created tags are private, numeric tag names cannot be addressed.
TDD evidence, honestly: red first (failing for the intended reason): custom_values limits, adapter user_can_change, contract tests for limits/one-decimal/links, all new service tests (16 failed before the editor change), tool tests (confirm, implicit_tags, description), schema guard null case. Written together with the code, not run red: tests/services/test_tag_hierarchy.py (pure helper, impl and test created in one step) and the e2e additions (written after the code, green on first run unless listed in the final results). In the first round the service/tool/e2e tests were also mostly red by import error only, and the e2e file passed on its first run.

Live correction found by the full e2e run: an update's PATCH answer does not carry user_can_change (only a read does), so DocumentRecord.user_can_change is bool | None and the pre-check refuses only on False; and user_can_change is the OBJECT permission only (the reader gets True on the unowned public document; the missing change_document permission is a separate 403 on the update, with Paperless's generic message). The contract suite pins both. Mutation check done: with the cache invalidation loop removed, the two invalidation tests fail.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
update_document (core) changes title, correspondent, document type, storage path (name or id), created date, ASN and owner, adds/removes/sets tags and sets/removes custom fields by name, and never drops data the caller did not ask to change: it reads the document, merges custom fields and tags, sends only what differs (no-op when nothing does) and returns the updated detail plus implicit_tags. Tags follow Paperless's live-verified hierarchy: wanted tags are closed over ancestors (idempotent tags_set), and a removal that would also drop child tags is rejected unless the children are removed explicitly (pure helpers in services/tag_hierarchy.py for PLMCP-22). Custom-field values are validated per data type before sending (select by label, monetary with currency, int32, url, strict dates, booleans, document links that must be visible; links are mirrored by Paperless, which the description states). Everything predictable (title, change permission, owner id, links) is checked before create_missing_tags creates any tag; later failures name created tags. Owner changes require confirm=true (new decision-12, supplements decision-6). Caches are invalidated for affected kinds. New DocumentWriter.update_document and TaxonomyWriter.create_tag ports with adapter, FakePaperless (PATCH replaces lists wholesale, tag hierarchy, link mirroring) and contract tests on fake and live 3.4.2. The read-then-PATCH race is documented (deferred note on PLMCP-37). Verified: just check 980 passed; just e2e 147 passed; just mutation 100 % (exit 0). Independent review: 1 Critical, 4 Important and 8 Minor fixed. Deliberate: snapshot (tool surface, error-message snapshot), schema-guard deviations for PATCH ?fields=, the PATCH response, narrowed custom-field value requests and the POST /api/tags/ response; e2e seed reconciles stale back-links; test_restricted_user_cannot_see_private_objects robust to foreign objects. DoD #1 left unchecked: tag_hierarchy tests and the e2e additions were written with or after the code. DoD #2 and #4 follow from CI and the merge.
<!-- SECTION:FINAL_SUMMARY:END -->
