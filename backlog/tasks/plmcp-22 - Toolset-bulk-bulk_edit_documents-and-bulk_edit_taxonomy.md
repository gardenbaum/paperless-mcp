---
id: PLMCP-22
title: 'Toolset bulk: bulk_edit_documents and bulk_edit_taxonomy'
status: To Do
assignee: []
created_date: '2026-11-09T19:26:00Z'
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
