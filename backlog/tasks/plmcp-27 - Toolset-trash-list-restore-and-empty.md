---
id: PLMCP-27
title: 'Toolset trash: list, restore and empty'
status: Done
assignee:
  - '@claude'
created_date: '2026-11-09T19:26:00Z'
updated_date: '2026-11-13T18:34:00Z'
labels: []
milestone: m-3
dependencies:
  - PLMCP-17
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 27000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Deleted documents go to the trash; restoring must be easy and emptying must never happen by accident (design §7).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 list_trash, restore_from_trash and empty_trash exist
- [x] #2 empty_trash requires explicit ids or all=true, and confirm in both cases; it never defaults to everything
- [x] #3 Adapter tests are schema-guarded and E2E covers the main flow on seeded data
- [x] #4 FakePaperless and the contract suite cover every new port method
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
1. Domain: domain/trash.py (TrashedDocument, TrashPage, TrashList, TrashRestored, TrashEmptied); ports TrashReader.list_trash and TrashWriter.restore_from_trash/empty_trash (always explicit ids; Paperless empties the whole trash when 'documents' is omitted, never sent).
2. Adapter paperless/trash.py on /api/trash/ (GET paginated full document rows, POST {action, documents}). Schema-guard entries for the wrong 3.4.2 response schemas (GET/POST 200 and the text/html 403).
3. FakePaperless: trash listing, restore (re-attaches trashed versions), empty; FakeDocument gets deleted_at/root_id.
4. Contract tests on fake and live 3.4.2 (restore of documents and versions, empty, 400s, 403s, pagination).
5. Service services/trash.py: dedupe/limit ids, scan the trash to name entries, ConfirmationRequired naming what is destroyed for good; all=true XOR document_ids; restore invalidates taxonomy counts.
6. Tools toolset 'trash' (opt-in): list_trash, restore_from_trash, empty_trash(document_ids|all, confirm); annotations, registry, snapshot, import-linter contract, README.
7. E2E over MCP on seeded live stack with plmcp27- fixtures; never empty the whole trash live (all=true on the fake only).
8. Gates: just check, just mutation, flock just e2e. Strict TDD, red first at each level.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
From PLMCP-17: delete_document (services/removal.py) tells the user the document is in the trash and can be restored in the web UI "or with restore_from_trash if the trash toolset is enabled"; the RESTORE constant there already names restore_from_trash, so make that tool real (same name) or update RESTORE. A test pins the wording in tests/services/test_notes_and_removal.py. Live-verified: trash entries stay until emptied; POST /api/trash/ {action: restore|empty, documents: [ids]} answers 200 {"result": "OK", "doc_ids": [...]}; GET /api/trash/ is paginated (page_size works). Contract tests (tests/contract/conftest.py) now have Backend.in_trash and delete_and_empty_trash helpers to reuse. empty_trash must use ConfirmationRequired (decision-15).

Progress: domain/trash.py, ports TrashReader/TrashWriter, adapter paperless/trash.py, FakePaperless trash (list/restore/empty, trashed versions are entries that re-attach), service services/trash.py, tools/trash.py (toolset 'trash', opt-in; default profile unchanged at 18/20), import-linter independence contract extended, README row. TDD order: adapter, contract (fake+live), service and tool tests were each run red before their code. The e2e test (tests/e2e/test_trash.py) was written after the code, not before. Live-verified on 3.4.2 (Paperless source read in the container too): GET /api/trash/ lists full document rows with deleted_at, paginated, owner/unowned entries only (no view grants), needs view_document; POST needs delete_document; ids not in trash or duplicated -> 400 for the whole request; POST without 'documents': restore -> 500, empty -> empties EVERYTHING the user sees, so the adapter never sends fewer than one id and all=true is resolved by listing the trash and naming ids; a deleted version is its own trash entry (id = version id, root_document = document); restoring it re-attaches it with its label and it is the newest version again (document content reverts to it); restoring a version whose document is in the trash -> 400 'Restore the root document instead'; restoring the document brings its versions back; emptying a document removes its versions. Deliberate changes: schema_guard WRONG_RESPONSE_SCHEMAS gets ('POST','/api/trash/',403) (text/html 'Insufficient permissions'); tests/tools/__snapshots__/test_surface.ambr regenerated: the 'all' and 'read-only' profiles gain the trash tools (405 added lines, nothing removed; default profile snapshot unchanged); FakeDocument gained deleted_at/root_id and the contract Backend's fake id counters now count trash entries too (ids were reused after a delete).

Gates so far: just check exits 0 (100% diff coverage). just mutation exits 0: full run 99.6% before, then the killable trash survivors were killed (message/hint assertions, MAX_IDS and SHOWN and max_all boundaries, multi-page lookups); 3 true equivalents listed in mutation-equivalents.toml with reasons (deleted=None vs False in _described, which is only tested for truthiness, twice; lowercase 'post' that httpx2 upper-cases). Final mutation score 100.0% (gate 80%); 2 unreviewed survivors remain that are not in the trash code and were there before. Fake follow-up from a live probe: a version deleted BEFORE its document stays an entry of its own when the document is restored (only versions deleted together with the document come back with it), so FakeDocument has with_root; the contract test covers it on both backends (fake adjusted together with that test after the probe, not red first). Full e2e first run: 377 passed, 2 failed: test_more_like_finds_other_documents[live] (stale search index on the shared stack, not touched by this task) and tests/e2e/test_stdio.py::test_the_environment_selects_the_tools_over_stdio, which was mine (read-only 'all' now also offers list_trash); fixed in the test, rerun of the full e2e follows. Live wall time of the new tests on the shared stack (trash bloated with ~940 foreign entries, each full trash scan ~3.3 s): contract 4 tests ~62 s, e2e 2 tests ~40 s; on a clean stack the scans are near-free.

Final: full e2e rerun after the stdio test fix: 378 passed, 6 skipped (AI), 1 failed: test_more_like_finds_other_documents[live] (empty more-like result on the shared stack, stale search index; unrelated to trash). Committed as 2b2dda9 on plmcp-27-trash; not pushed.

Fix round after review (rebased onto origin/main 8727918, union-merged with views; snapshot regenerated from main's, diff vs main is additions only). I1: e2e all=true refusal reads the trash count first and asserts the matching refusal (the max_all Validation above 1000, else the ALL ConfirmationRequired), then that nothing changed. I2: TrashService takes web_url; restored entries (RestoredEntry) carry web_url (a version links its document); list_trash carries web_url of the web UI's /trash page (the 3.4.2 frontend has the route 'trash'; entries have no per-entry page, so no per-entry link). M1: all=true reads the trash in one request (page_size = max_all+1) so count and ids agree, entries deduplicated by id (test with a reader that repeats an entry); _entries already collects into a dict by id. M2: description, refusal and success message say all=true empties what the trash holds when confirmed and steer to document_ids. M3: the fake orders by created desc, id desc (observed tie order) and service tests no longer pin an order. M4: contract tests for an emptied document taking its earlier trashed versions along, and an unowned entry visible to a non-owner; empty_trash docstring says so. M5: _counted in the ALL head and message, qualifier kept, list_trash says a superuser sees every entry. M7: both tools take ids ge=1, empty_trash also min/max length 100 (the empty-list service check stays for service callers). Mutation: full 100.0%, trash survivors only the 3 listed equivalents (hashes updated). e2e full: 408 passed, 6 skipped, 1 failed: the known test_more_like_finds_other_documents[live] (PLMCP-40).

Controller: review fix round accepted (I1, I2, M1-M5, M7). DoD #1 left unchecked: the E2E test, the M4 contract additions and the fake ordering change were written together with the code. Gates on 6e53a95 (rebased onto 8727918): just check 0 (100% diff), just mutation 100.0%, e2e 408 passed, 1 failed (test_more_like_finds_other_documents[live], PLMCP-40).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Adds the opt-in trash toolset: list_trash (read-only, links the web UI trash page), restore_from_trash (1-100 ids, all must be in the trash; restored entries carry web_url; versions re-attach to their document) and empty_trash, which needs explicit ids or all=true plus confirm and never sends a request without ids, since Paperless empties everything for a POST without documents. all=true reads the trash in one request, stops at 1000 entries and empties what the trash holds at the confirmed call. The fake models trashed versions, restore and emptying, ordered like 3.4.2. Schema guard: one WRONG_RESPONSE_SCHEMAS entry for the text/html 403 of POST /api/trash/. Default profile unchanged (18 tools); the surface snapshot only gains trash tools in the all and read-only profiles.
<!-- SECTION:FINAL_SUMMARY:END -->
