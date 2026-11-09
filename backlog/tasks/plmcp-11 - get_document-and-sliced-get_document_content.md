---
id: PLMCP-11
title: get_document and sliced get_document_content
status: To Do
assignee: []
created_date: '2026-11-09T19:25:00Z'
labels: []
milestone: m-1
dependencies:
  - PLMCP-9
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 11000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Reading one document must be cheap in tokens and readable for an LLM: names instead of ids, custom fields rendered by data type, and OCR text only in slices (design §5.4, §7).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 get_document returns names, custom fields of every data type rendered readably (select label, monetary with currency, date, document link, boolean, url, integer, float, string, long text), notes count and web_url
- [ ] #2 include adds notes, history (without OCR text), file metadata, versions and classic suggestions on request
- [ ] #3 get_document_content returns a slice by offset and max_chars (default 20000) with total_chars and next_offset; an offset past the end returns an empty slice with total_chars
- [ ] #4 A document with more than 1000000 characters is served slice by slice
- [ ] #5 paperless://documents/{id} and paperless://documents/{id}/content resources exist
- [ ] #6 Missing documents and documents the credential cannot see return client-safe NotFound errors with a hint
- [ ] #7 FakePaperless and the contract suite cover every new port method
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
