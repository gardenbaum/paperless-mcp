---
id: PLMCP-16
title: update_document with merge semantics
status: To Do
assignee: []
created_date: '2026-11-09T19:25:00Z'
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
