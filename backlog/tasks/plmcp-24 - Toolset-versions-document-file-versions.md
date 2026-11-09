---
id: PLMCP-24
title: 'Toolset versions: document file versions'
status: To Do
assignee: []
created_date: '2026-11-09T19:26:00Z'
labels: []
milestone: m-3
dependencies:
  - PLMCP-18
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 24000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Paperless 3.x stores file versions per document and can merge documents as versions (design §6).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 list_document_versions, upload_document_version, set_document_version_label, delete_document_version (confirm) and merge_documents_as_versions exist
- [ ] #2 Version uploads follow the same size, MIME and path rules as upload_document
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
