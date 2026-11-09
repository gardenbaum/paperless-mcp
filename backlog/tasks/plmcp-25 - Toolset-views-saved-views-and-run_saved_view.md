---
id: PLMCP-25
title: 'Toolset views: saved views and run_saved_view'
status: To Do
assignee: []
created_date: '2026-11-09T19:26:00Z'
labels: []
milestone: m-3
dependencies:
  - PLMCP-10
  - PLMCP-17
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 25000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Saved views are how the user organises Paperless; running one requires translating its filter rules (about 40 rule types) into a document query, as the Paperless frontend does (design §6, §14).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Saved views list, get, create, update and delete (confirm) by name or id
- [ ] #2 run_saved_view returns the documents of a view with the same rows and paging as search_documents
- [ ] #3 Every filter rule type of Paperless 3.4.2 is either translated or rejected with an explicit error naming the rule type; a test lists the covered rule types
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
