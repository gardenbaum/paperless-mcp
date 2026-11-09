---
id: PLMCP-32
title: 'Toolset raw: read-only paperless_api_get'
status: To Do
assignee: []
created_date: '2026-11-09T19:26:00Z'
labels: []
milestone: m-4
dependencies:
  - PLMCP-15
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 32000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
An escape hatch for reading anything the curated tools do not cover, so the user can do everything possible. It must not become a way to write or to reach other hosts (design §6, anti-goals).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 paperless_api_get(path, params) performs GET only, for relative paths under /api/; absolute URLs, scheme or host changes and path traversal are rejected
- [ ] #2 Responses are size-capped and truncation is reported
- [ ] #3 The toolset is off by default
- [ ] #4 Adapter tests are schema-guarded and E2E covers the main flow on seeded data
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
