---
id: PLMCP-11
title: get_document and sliced get_document_content
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-09T19:25:00Z'
updated_date: '2026-11-12T18:03:00Z'
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
- [ ] #1 get_document returns names, custom fields of every data type rendered readably (select label, monetary with currency, date, document link, boolean, url, integer, float, string, long text), notes count and web_url
- [ ] #2 include adds notes, history (without OCR text), file metadata, versions and classic suggestions on request
- [ ] #3 get_document_content returns a slice by offset and max_chars (default 20000) with total_chars and next_offset; an offset past the end returns an empty slice with total_chars
- [ ] #4 A document with more than 1000000 characters is served slice by slice
- [ ] #5 paperless://documents/{id} and paperless://documents/{id}/content resources exist
- [ ] #6 Missing documents and documents the credential cannot see return client-safe NotFound errors with a hint
- [ ] #7 FakePaperless and the contract suite cover every new port method
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
<!-- SECTION:NOTES:END -->
