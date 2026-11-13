---
id: PLMCP-23
title: >-
  Toolset pdf: rotate, merge, split, delete pages, edit, remove password,
  reprocess
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-09T19:26:00Z'
updated_date: '2026-11-13T19:27:00Z'
labels: []
milestone: m-3
dependencies:
  - PLMCP-13
  - PLMCP-17
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 23000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Paperless 3.x offers server-side PDF operations through dedicated endpoints; they run as tasks and some destroy the originals (design §6).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Tools rotate_documents, merge_documents, split_document, delete_pages, edit_pdf, remove_document_password and reprocess_documents exist
- [ ] #2 Each returns the resulting task; operations that delete originals or pages require confirm
- [ ] #3 Page ranges are validated against the page count before sending
- [ ] #4 Adapter tests are schema-guarded and E2E covers the main flow on seeded data
- [ ] #5 FakePaperless and the contract suite cover every new port method
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
1. Probe live 3.4.2 (merge, rotate, edit_pdf, remove_password, reprocess): response shape, task, which operations delete originals or replace the document.
2. Domain: domain/pdf.py (page edits, result model), PdfWriter port in ports.py.
3. Adapter paperless/pdf.py (schema-guarded adapter tests first), FakePaperless + contract tests (fake and live) for each port method, test_coverage wiring.
4. Services: page range parsing validated against the page count, PdfService (confirm via ConfirmationRequired.for_effect for deletions, task follow via task_outcome).
5. Tools tools/pdf.py (7 tools, toolset pdf, opt-in), annotations, TOOL_TOOLSETS, server wiring, import-linter independence contract, surface snapshot, README.
6. E2E on seeded multi-page PDFs; just check (100% diff coverage), just mutation, full e2e under flock.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Probe on live 3.4.2 (shared stack, own plmcp23-* documents, all deleted and trash emptied afterwards):
- Every PDF endpoint (rotate, merge, edit_pdf, remove_password, reprocess) answers 200 {"result": "OK"}: NO task id comes back. The work runs as tasks that show up in /api/tasks/ within ~30 ms: consume_file (trigger api_upload) per produced file with input_data.filename {id}_rotated.pdf, {id}_edit_{n}.pdf (new documents), {id}_edited.pdf (update_document), {ids}_merged.pdf, {id}_unprotected.pdf; reprocess is one task_type reprocess_document per document (no related ids, no input_data); delete_original / delete_originals adds a trailing bulk_delete task. So the adapter finds the task ids itself: highest task id before the POST, then poll the list for newer rows that match the operation (type + filename), bounded; a user who may not view tasks (403, e.g. reader) gets no ids and the service reports state unknown, like an upload.
- Not destructive: rotate (adds a NEW VERSION to the same document, old version kept), edit_pdf/remove_password with update_document (new version, old kept; delete_original is silently ignored then), edit_pdf/remove_password/merge without delete flags (new documents, originals untouched).
- Destructive: delete_original(s) -> the original document goes to the TRASH (bulk_delete task, 404 afterwards, restorable). I treat as confirm-required: merge delete_originals, split/edit_pdf/remove_password with replace_original, and delete_pages / edit_pdf that drops pages from the file a document shows (earlier version stays, but the document's file loses pages).
- Validation facts: page out of range -> 400 'Page 9 is out of bounds for document with 3 pages.'; edit_pdf takes exactly one document; update_document needs a single output document; 'doc' index must be contiguous from 0 ('doc index is out of bounds'); rotate/rotate-in-edit must be multiples of 90 (negative accepted); empty operations 400; wrong password 400 text 'Error performing password removal, check logs for more detail.'; merge with unknown/hidden ids 400 documents error; permission failures are 403 text/html 'Insufficient permissions' (hidden and view-only alike); encrypted PDFs are consumed with page_count null and no text (so page-based ops need page_count); duplicates of a page in edit_pdf are accepted.
- Reprocess decision: bulk_edit_documents already has operation reprocess (via the deprecated bulk_edit form, previewable). AC #1 names reprocess_documents in pdf, so pdf gets reprocess_documents on the dedicated /api/documents/reprocess/ endpoint (v10 route, remote_ocr option, returns the task). Bulk's operation stays untouched (PLMCP-22 is merged; its description and surface are not changed here); the two overlap only when both toolsets are enabled, and the pdf description points out they do the same. Default profile unchanged (pdf is opt-in).

Implementation: domain/pdf.py + PdfWriter port; paperless/pdf.py (finds the tasks of a request: newest task id before the POST, then bounded poll of /api/tasks/ for newer rows matching type and filename; a user who may not view tasks gets no ids); FakePaperless pdf methods (permissions as probed); services/pdf.py + services/page_ranges.py (ranges parsed and checked against page_count: 'Page 9 does not exist: the document has 3 pages'; non-PDF and unknown page count (encrypted) refused; confirm via ConfirmationRequired.for_effect); tools/pdf.py (7 tools, toolset pdf, opt-in, default profile stays 18 tools); task_outcome.follow gains document_ids/accepted; import-linter independence contract lists tools.pdf; README; surface snapshot regenerated deliberately (7 new tools; every removed line reappears: only reordering by name); tests/tools/test_visibility.py: its synthetic toolset was named 'pdf', renamed 'scratch' now that a real pdf toolset ships.
Permissions probed on 3.4.2 (non-owner with a change grant): rotate, edit_pdf and (assumed, same family) remove_password need ownership (or no owner / superuser) - a change grant is 403; merge and reprocess accept a change grant; edit_pdf/merge into new documents need add_document; delete_original(s) need ownership and delete_document. A user without view_paperlesstask (403 on /api/tasks/) still gets the work done but no task ids. Extra live finding: merge_documents accepts a metadata_document_id outside the documents (not validated by Paperless; the service validates it). Mutation/check/e2e results follow.

Correction to the probe notes: merge is not validated against visibility before permissions. A hidden or view-only document makes merge a 403 like the others (the earlier 400 in my probe came from a duplicated id in the request). Order on 3.4.2 for all five: global change_document (403), ids (400), object permissions (403). Contract-tested live: a user holding only a change grant (not the owner) can reprocess but not rotate/edit/remove_password, can merge only into new documents (needs add_document), and cannot delete originals (ownership + delete_document); without view_paperlesstask a request still works and returns no task ids.
Tests: red first at every level (page_ranges, adapter with schema guard, fake + contract suite on fake and live 3.4.2, service, tool, e2e); the mutation run was used to harden the exact-message tests; mutation-equivalents.toml got two reviewed entries (pdf=None is falsy like False; round to 2 decimals of a sum of tenths equals round to 1). Decision: reprocess_documents is in pdf per AC #1 and uses the dedicated v10 endpoint; bulk_edit_documents keeps its reprocess operation untouched (PLMCP-22 is merged) - the two overlap only if both toolsets are on, and the pdf tool's description says so.

Gate results (final code): just check exit 0 (2563 passed, total coverage 99.97 %, diff coverage 100 % on 429 lines); just mutation from a clean mutants/: 8944 killed + 6 timeout, 2 survived unreviewed, 108 reviewed equivalent, score 100.0 % (gate 80 %) - the 2 unreviewed survivors are BulkDocumentEditor._current mutants 6 and 7 from PLMCP-22, not touched here; three new equivalent entries (pdf=None falsy like False in reprocess; round(sum of tenths, 2) = round(., 1); 'post' lower case upper-cased by httpx2). Full flock just e2e: 426 passed, 6 skipped (AI), 1 failed = test_more_like_finds_other_documents[live] (stale Whoosh index, PLMCP-40, known, not worked around); the 19 live contract tests of PdfWriter and the 5 e2e tests of the pdf toolset pass. Probe/test documents on the shared stack are deleted and their trash emptied. Open for the controller: the AC and Definition of Done are not checked and the task is not Done (finalization is the controller's step).
<!-- SECTION:NOTES:END -->
