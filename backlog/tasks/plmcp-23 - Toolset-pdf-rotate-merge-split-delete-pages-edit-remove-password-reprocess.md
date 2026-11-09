---
id: PLMCP-23
title: >-
  Toolset pdf: rotate, merge, split, delete pages, edit, remove password,
  reprocess
status: To Do
assignee: []
created_date: '2026-11-09T19:26:00Z'
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
