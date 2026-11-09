---
id: PLMCP-10
title: search_documents with structured filters, full text and paging
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
ordinal: 10000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The main entry point for finding documents. It must accept names, dates and custom-field queries, pass Paperless's Tantivy full-text syntax through unchanged, and never return OCR text in result rows (design §5.4, §6, §7).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Filters: full text, more-like id, title contains, correspondent, document type and storage path by name or id, tags all/any/none, created and added date ranges, owner, inbox, custom-field query including relative dates; ordering; page and page_size (default 25, max 100)
- [ ] #2 Rows contain id, title, dates, correspondent/type/storage path/tag names, readable custom-field values and web_url, never OCR content; full-text results include highlights
- [ ] #3 The result reports the total count and the next page; nothing beyond the requested page is fetched
- [ ] #4 Unknown or ambiguous names and invalid filter combinations return Validation, NotFound or Ambiguous errors with a hint
- [ ] #5 E2E covers name filters, date ranges and full text on seeded data
- [ ] #6 FakePaperless and the contract suite cover every new port method
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
