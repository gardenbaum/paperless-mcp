---
id: PLMCP-18
title: upload_document for local and remote clients
status: To Do
assignee: []
created_date: '2026-11-09T19:25:00Z'
labels: []
milestone: m-2
dependencies:
  - PLMCP-13
  - PLMCP-16
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 18000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Uploads must work for Claude Code today and remote clients later, without exposing the host filesystem: one reference server lets any HTTP caller read arbitrary server paths (design §5.7).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Accepts base64 content up to a configurable size limit, or in stdio mode only a path inside configured allowlisted directories (symlinks resolved, traversal rejected)
- [ ] #2 Metadata by name: title, correspondent, document type, storage path, tags, created date, archive serial number, custom fields with values
- [ ] #3 Size, MIME type and filename are validated; non-ASCII filenames are preserved
- [ ] #4 Returns the consumption task; an optional bounded wait returns the new document id
- [ ] #5 The path parameter is absent from the tool schema outside stdio mode (registry test)
- [ ] #6 E2E uploads a PDF and finds it afterwards
- [ ] #7 FakePaperless and the contract suite cover every new port method
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
