---
id: PLMCP-11
title: get_document and sliced get_document_content
status: Done
assignee:
  - '@claude'
created_date: '2026-11-09T19:25:00Z'
updated_date: '2026-11-12T18:19:00Z'
labels: []
milestone: m-1
dependencies:
  - PLMCP-9
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 11000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Reading one document must be cheap in tokens and readable for an LLM: names instead of ids, custom fields rendered by data type, and OCR text only in slices (design §5.4, §7).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 get_document returns names, custom fields of every data type rendered readably (select label, monetary with currency, date, document link, boolean, url, integer, float, string, long text), notes count and web_url
- [x] #2 include adds notes, history (without OCR text), file metadata, versions and classic suggestions on request
- [x] #3 get_document_content returns a slice by offset and max_chars (default 20000) with total_chars and next_offset; an offset past the end returns an empty slice with total_chars
- [x] #4 A document with more than 1000000 characters is served slice by slice
- [x] #5 paperless://documents/{id} and paperless://documents/{id}/content resources exist
- [x] #6 Missing documents and documents the credential cannot see return client-safe NotFound errors with a hint
- [x] #7 FakePaperless and the contract suite cover every new port method
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [x] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Domain (domain/documents.py): DocumentRecord/Note/DocumentVersion/HistoryEntry/FileMetadata/Suggestions (port rows, ids), DocumentDetail + DocumentHistory + SuggestionsView (tool output, names), ContentSlice, Include literal. Ports on DocumentReader: get_document, get_document_content, list_document_notes, list_document_history, get_document_metadata, get_document_suggestions.
2. Verified live on 3.4.2 first: detail 404 for missing AND hidden docs; /notes/ /history/ need extra global perms (403 for the restricted reader); metadata/suggestions 403 on hidden docs; history is a bare list (schema says paginated) whose create entry carries the OCR text under changes.content (strip it); detail has versions[] and notes[].
3. TDD per layer, red then green: adapter tests (schema-guarded recorded responses; add WRONG_RESPONSE_SCHEMAS entry for history), FakePaperless methods + contract tests (fake and live; seed gets one note on the public document), service tests (DocumentService.get/content: names, custom fields, include, unavailable sections, slicing incl. >1M chars and offset past end), tool tests (get_document, get_document_content, resources paperless://documents/{id} and /content), E2E through the assembled server (incl. a >1M-char throwaway document).
4. Tools in tools/core.py + ANNOTATIONS entries; snapshot regenerated deliberately (2 tools + 2 resource templates). 5. just check, flock just e2e, commit.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Live-verified on Paperless 3.4.2 (E2E stack) before encoding it in the fake: GET /documents/{id}/ is 404 for a missing AND a hidden document (blind user without view_document: 403); the detail view returns versions[] and notes[] (notes content is visible even to a user without view_note, so notes_count comes from the detail while include=notes uses /notes/, which honours view_note); /notes/ needs view_note and /history/ needs view_logentry (403 for the restricted reader), metadata/ and suggestions/ need only document visibility; on those four sub-resources a missing document is 404 but a hidden one is 403 (checked with a temporary user holding view_document+view_note+view_logentry, deleted again); /history/ answers a bare list (the schema says paginated), ignores page_size, has actor null for system changes, and the 'create' entry carries the whole OCR text under changes.content (the adapter drops that key). Metadata lang/barcodes/archive_metadata are not mapped (token economy).
Design choices: six new DocumentReader methods (get_document, get_document_content, list_document_notes, list_document_history, get_document_metadata, get_document_suggestions); versions come from the detail (no extra call); a sub-resource the user may not read goes into DocumentDetail.unavailable instead of failing the call; history is capped at the newest 50 entries with total/truncated; content is fetched whole (fields=content) and sliced in DocumentService (default 20000, tool max 100000); resources paperless://documents/{id} (detail JSON) and paperless://documents/{id}/content{?offset,max_chars} (ContentSlice JSON, out-of-range query values are a ResourceError); owner is a Ref named through the user list when the user may list users.
Deliberate gate/schema-guard/snapshot changes: tests/adapter/schema_guard.py WRONG_RESPONSE_SCHEMAS gets two entries (GET /api/documents/{id}/ 200: fields= drops required props and custom field values are bool/list; GET /api/documents/{id}/history/ 200: bare list and null actor); tests/tools/__snapshots__/test_surface.ambr regenerated with --snapshot-update: pure additions (tools get_document, get_document_content; resource templates paperless://documents/{document_id} and .../content{?offset,max_chars}), no existing entry changed; the E2E seed (tests/e2e/helpers.py, mirrored in fakes.paperless.seeded) gains one note 'e2e remark zebra' on the public document (search tests unaffected). No threshold or decision touched.
Test levels: adapter, contract (fake and live), service, tool written/run red first (AttributeError / ImportError / snapshot mismatch), then green. Not run red first: the E2E file tests/e2e/test_documents.py (written after the code, passes on first run) and the content-resource range check test (written before the check was added as clamping, then changed to ResourceError).

Gates: just check green (397 passed, 99.92% coverage, diff-cover 100%); just e2e green (72 passed, 8 of them in the new tests/e2e/test_documents.py incl. a throwaway 1,050,000-character document read in 11 slices). One earlier e2e run failed test_seeding_idempotent (documents count 3 != 2) because PLMCP-13 had left a task document on the shared stack, and two runs hit 429 on /api/token/; both unrelated to this change.

Review fix round 1.
I1: confirmed in the container (documents/views.py history): with PAPERLESS_AUDIT_LOG_ENABLED=false the view answers HttpResponseBadRequest('Audit log is disabled') before any other check. New domain error FeatureUnavailable (it was named in the design but did not exist); the adapter maps the 400 on /history/ to it (the only 400 that endpoint has), DocumentService treats it like PermissionDenied (section goes to unavailable). Fake toggle FakeState.audit_log_enabled; adapter, fake and service tests.
I2: verified live (temporary user with view_document+view_logentry+view_note, a view grant on admin-owned doc 2, then reset and user deleted): detail/notes/metadata 200 but /history/ 403 ('Insufficient permissions'); source: history needs view_logentry AND root document unowned, own, or superuser, an object view grant is not enough. A missing document is 404 even without view_logentry (get happens first); order is view_document, audit enabled, existence, view_logentry+ownership. Encoded in the fake's list_document_history; contract case added (reader, missing -> NotFound, live+fake); the viewer-grant case is fake-only (no seeded user holds view_logentry) in tests/fakes/test_paperless.py with the live result in its comment.
I3: MAX_SLICE = DEFAULT_SLICE = 20000 (FastMCP sends results twice, content and structured; clients cap tool output near 25k tokens; design 7 says slices of 20 000); the parameter description is generated from the constant. E2E >1M document: chains the first 3 slices through next_offset and reads the final slice (offset 1,040,000, 10,000 chars, next_offset null).
I4: get_document renders document-link fields as [{id, name}] using one new port method get_document_titles (GET /documents/?id__in=..&fields=id,title&page_size=n; hidden/missing ids are simply absent -> name null, verified live), capped at 100 distinct linked ids (the rest keep name null). Ruling: search rows keep document-link ids unchanged (no extra request per page).
Minor 5: get_document_content fetches the whole text from Paperless for every slice (no cache); accepted trade-off for now (up to ~1 MB per call, served from Paperless's DB), a cache can come with the per-credential cache work if it proves slow.
Minor 6: referred kinds are fetched with asyncio.gather; TaxonomyResolver now caches a PermissionDenied per credential and kind for the TTL, so a denied /api/users/ is not repeated (test added).
Minor 7: tools/_shared.resource_boundary (domain errors -> ResourceError with message+hint logged at INFO; other exceptions masked, type-only ERROR log) used by the taxonomy and both document resources; tests for both paths. Minor 8: hint asserted in resource test. Minor 9: try right after ensure_document. Minor 10: docstrings fixed (get_document_content raises PermissionDenied; history raises FeatureUnavailable and states the ownership rule; notes order claim). Minor 11 and 12: recorded as known gaps per the coordinator.
Snapshot regenerated deliberately: 5 insertions / 4 deletions in test_surface.ambr (max_chars limit 20000 text/schema, get_document description for document links and audit log).

Rebased onto origin/main (33a232a, PLMCP-13): conflicts in domain/ports.py, tools/_shared.py, tools/core.py and tests/fakes/paperless.py resolved by keeping both sides (TaskReader and the task tool stay; the new DocumentReader methods stay in DocumentReader). Surface snapshot re-generated with --snapshot-update on the rebased branch: no diff against the merged one. After the rebase just check green (452 passed, 99.93%, diff-cover 100%) and flock just e2e green (82 passed).

Known gaps (review minors 11, 12): suggestions are tested live only for the empty case (no trained classifier on the E2E stack); the id-to-name mapping is covered through the fake. A monetary value stored without a currency code passes through unchanged, so 'monetary with currency' holds when Paperless stores a code; history 'changes' keep raw ids for tags and correspondents.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
get_document (core) returns one document with names instead of ids (correspondent, type, storage path, tags, owner when the user may list users), custom fields rendered by data type (select label, monetary with currency, dates, booleans, urls, numbers, text, document links as id and title), notes count, versions and web_url; include adds notes, history (newest 50, OCR text dropped), file metadata (scalar facts), versions and classic suggestions; a section the credential may not read or that is disabled (audit log off) is listed in unavailable instead of failing. get_document_content returns a slice by offset and max_chars (default and max 20000, as the design's token economy) with total_chars and next_offset; an offset past the end is an empty slice; a document over 1 000 000 characters is served slice by slice (E2E). Resources paperless://documents/{id} and .../content{?offset,max_chars} behind a new resource error boundary (also used by the taxonomy resource). Missing and hidden documents are NotFound with a hint. Seven new DocumentReader methods with adapter, FakePaperless (history ownership rule verified live) and contract tests on fake and live 3.4.2; new FeatureUnavailable error; TaxonomyResolver caches a PermissionDenied per credential and kind. Verified: just check 452 passed, 99.93 %; just e2e 82 passed. Independent review: 4 Important and 8 Minor fixed, 2 recorded as known gaps. Deliberate: two schema-guard deviations (GET /api/documents/{id}/ with fields=, /history/ bare list), surface snapshot (two tools, two resource templates), a seeded note. DoD #1 left unchecked: the E2E file and the content-resource range test were green on their first run. DoD #4 is checked by the merge itself.
<!-- SECTION:FINAL_SUMMARY:END -->
