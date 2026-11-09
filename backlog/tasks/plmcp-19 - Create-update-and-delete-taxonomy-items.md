---
id: PLMCP-19
title: Create, update and delete taxonomy items
status: To Do
assignee: []
created_date: '2026-11-09T19:25:00Z'
labels: []
milestone: m-2
dependencies:
  - PLMCP-9
  - PLMCP-17
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 19000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Filing documents needs new tags, correspondents and fields on the fly (design §6, core write).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 create, update and delete for tags (colour, parent, matching), correspondents, document types, storage paths (path template) and custom fields (data type, select options)
- [ ] #2 Items are addressed by name or id; delete requires confirm
- [ ] #3 Changing select options of a custom field keeps existing option ids stable
- [ ] #4 The taxonomy cache is invalidated after every write
- [ ] #5 FakePaperless and the contract suite cover every new port method
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
