---
id: PLMCP-16
title: update_document with merge semantics
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-09T19:25:00Z'
updated_date: '2026-11-12T19:15:00Z'
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
- [ ] #1 Updates title, correspondent, document type, storage path (by name or id), created date, archive serial number and owner; tags add, remove or set; custom fields set or remove by name
- [ ] #2 Setting one custom field never removes others (tested against the fake and live Paperless)
- [ ] #3 Values are validated per custom-field data type before sending (select by label, monetary with currency, document-link ids, dates)
- [ ] #4 Unknown tag names fail with NotFound and a hint unless create_missing_tags is set
- [ ] #5 Returns the updated document detail and invalidates affected cache entries
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
<!-- SECTION:NOTES:END -->
