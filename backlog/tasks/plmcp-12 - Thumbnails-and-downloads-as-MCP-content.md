---
id: PLMCP-12
title: Thumbnails and downloads as MCP content
status: To Do
assignee: []
created_date: '2026-11-09T19:25:00Z'
labels: []
milestone: m-1
dependencies:
  - PLMCP-11
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 12000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Vision-capable clients can read thumbnails, and originals or archive PDFs must be reachable without touching the host filesystem (design §5.5, §5.7).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 get_document_thumbnail returns MCP image content
- [ ] #2 download_document (original or archive) returns an embedded resource up to a configurable size cap; above it, metadata and web_url only
- [ ] #3 paperless://documents/{id}/thumbnail, /original and /archive resources exist
- [ ] #4 MIME type and filename, including non-ASCII names, are preserved
- [ ] #5 FakePaperless and the contract suite cover every new port method
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
