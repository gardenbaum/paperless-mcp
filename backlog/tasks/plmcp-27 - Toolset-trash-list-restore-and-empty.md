---
id: PLMCP-27
title: 'Toolset trash: list, restore and empty'
status: To Do
assignee: []
created_date: '2026-11-09T19:26:00Z'
updated_date: '2026-11-12T23:05:00Z'
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
- [ ] #1 list_trash, restore_from_trash and empty_trash exist
- [ ] #2 empty_trash requires explicit ids or all=true, and confirm in both cases; it never defaults to everything
- [ ] #3 Adapter tests are schema-guarded and E2E covers the main flow on seeded data
- [ ] #4 FakePaperless and the contract suite cover every new port method
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
From PLMCP-17: delete_document (services/removal.py) tells the user the document is in the trash and can be restored in the web UI "or with restore_from_trash if the trash toolset is enabled"; the RESTORE constant there already names restore_from_trash, so make that tool real (same name) or update RESTORE. A test pins the wording in tests/services/test_notes_and_removal.py. Live-verified: trash entries stay until emptied; POST /api/trash/ {action: restore|empty, documents: [ids]} answers 200 {"result": "OK", "doc_ids": [...]}; GET /api/trash/ is paginated (page_size works). Contract tests (tests/contract/conftest.py) now have Backend.in_trash and delete_and_empty_trash helpers to reuse. empty_trash must use ConfirmationRequired (decision-15).
<!-- SECTION:NOTES:END -->
