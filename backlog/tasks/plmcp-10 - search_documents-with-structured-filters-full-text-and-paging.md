---
id: PLMCP-10
title: search_documents with structured filters, full text and paging
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-09T19:25:00Z'
updated_date: '2026-11-12T16:56:00Z'
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
- [x] #1 Filters: full text, more-like id, title contains, correspondent, document type and storage path by name or id, tags all/any/none, created and added date ranges, owner, inbox, custom-field query including relative dates; ordering; page and page_size (default 25, max 100)
- [x] #2 Rows contain id, title, dates, correspondent/type/storage path/tag names, readable custom-field values and web_url, never OCR content; full-text results include highlights
- [x] #3 The result reports the total count and the next page; nothing beyond the requested page is fetched
- [x] #4 Unknown or ambiguous names and invalid filter combinations return Validation, NotFound or Ambiguous errors with a hint
- [x] #5 E2E covers name filters, date ranges and full text on seeded data
- [x] #6 FakePaperless and the contract suite cover every new port method
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Domain: documents.py with DocumentQuery (resolved ids), DocumentHit/DocumentPage (port rows: ids and raw custom-field values), DocumentSummary/DocumentSearchResult (tool output with Ref names, readable field values, highlights, web_url). TaxonomyItem.select_options becomes (id, label) pairs: select values are option ids and must render as labels (snapshot change).
2. Port DocumentReader.search_documents(query, page, page_size): one GET /api/documents/ with fields= (no content is ever fetched), query/more_like_id, __id__all/in/none CSV, created/added __date__ ranges, owner__id, is_in_inbox, custom_field_query as JSON, ordering, page/page_size.
3. FakePaperless implements it (visibility, filters, simple term matching for full text, more-like on shared words, a subset of custom_field_query incl. 'today', Paperless's 400 messages) plus contract tests on both backends.
4. Service DocumentService.search: resolve names via TaxonomyResolver, validate combinations (query with more_like_id, from after to, a tag in all and none), project rows (names, select labels, monetary, web_url from PAPERLESS_PUBLIC_URL or the URL); a kind the user may not view leaves names null.
5. Tool search_documents in core (annotation, boundary, page_size 1..100 default 25), surface snapshot, e2e test of name filters, date ranges and full text through the assembled server on the live stack.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented domain/documents.py, DocumentReader port, PaperlessDocumentReader, DocumentService, search_documents tool; FakePaperless.search_documents with a custom_field_query subset (tests/fakes/custom_field_query.py).
Verified live (3.4.2): fields= keeps __search_hit__ and drops content; query together with more_like_id is a 400 ('Specify only one of ...'), so the service rejects it first; custom_field_query accepts select labels and relative dates ('1 year ago', 'today'); bad expressions and unknown fields are 400 with field messages (-> Validation); a page beyond the last is 404 (-> NotFound); an unparsable Tantivy query returns 200 with no hits; more-like highlights are empty strings (mapped to null).
Deliberate changes: (1) TaxonomyItem.select_options is now (id, label) pairs instead of labels, because documents store the option id and rows must show the label; list_taxonomy output schema changes accordingly (surface snapshot updated). (2) Schema guard: new WRONG_RESPONSE_SCHEMAS entry for GET /api/documents/ 200, because fields= drops properties the schema marks required and custom field values can be booleans and id lists that its oneOf omits; the live contract suite checks those responses instead. (3) Surface snapshot: new search_documents tool.
Test order: contract tests were red first (missing module); adapter, service and tool tests were written before their code except the adapter tests, which followed the adapter draft.

Review (independent agent, live checks): fixed (1) owner by id no longer needs the user list (view_user); a username without it is PermissionDenied with a hint to pass the id; (2) live 403 for an unknown or hidden more_like_id (text/html) is now NotFound naming the id, mirrored by the fake, with a contract test; new schema-guard deviation for GET /api/documents/ 403; (3) a page beyond the last is NotFound with a hint; (4) note_highlights is used when only a note matches; (5) the fake orders by modified and archive_serial_number; (6) the fake ignores AND/OR/NOT in full text. Accepted, not fixed: the fake's custom_field_query rejects gt on monetary and documentlink contains (Paperless accepts them); more-like results depend on the live search index, so test_more_like_finds_other_documents may flake on a freshly seeded stack; tags in both tags_any and tags_none are not rejected (the result is just empty).

Review fixes: FakePaperless.search_documents now enforces the global view_document permission (PermissionDenied, as live 403); new seeded account e2e-blind (view_uisettings only) on fake and live, Backend.blind / Stack.blind_token. _ordered sorts by native value, so archive_serial_number is numeric; seeded ASNs 2 (public) and 10 (private). Contract tests added for both, red on the fake first, green on fake and live 3.4.2. Seed change: the private document's text now repeats '2026' because Paperless's more-like-this keeps only the 12 best-scoring terms and the new ASN pushed the one shared term out (live more_like returned nothing; the baseline seed was already flaky there). No gate, snapshot or decision weakened. Note: tests/e2e compose binds IPv6 and fails in IPv6-less sandboxes (PAPERLESS_BIND_ADDR=0.0.0.0 override used locally, not committed).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
search_documents (core toolset) finds documents by full text (Tantivy syntax passed through), more-like id, title, correspondent/document type/storage path/tags all/any/none and owner by name or id, created/added date ranges, inbox and Paperless's custom_field_query (relative dates included), with ordering and paging (default 25, max 100). One GET per call with fields=, so OCR content is never fetched; rows carry names, readable custom-field values (select labels, 'EUR 12.50'), highlights (also from notes) and web_url (PAPERLESS_PUBLIC_URL or PAPERLESS_URL). Names resolve via TaxonomyResolver (decision-5); contradicting filters, unknown/ambiguous names, rejected queries, hidden more-like ids and pages past the end are errors with hints. New DocumentReader port with adapter, FakePaperless implementation and contract tests on fake and live 3.4.2; e2e through the assembled server. Deliberate changes: TaxonomyItem.select_options now holds (id, label) pairs (list_taxonomy schema, snapshot); two schema-guard deviations for GET /api/documents/ (200 with fields=, 403 text/html). Risks: the more-like contract test depends on the live search index; the fake supports only a subset of custom_field_query. DoD #1 left unchecked: the adapter tests followed the adapter draft.
<!-- SECTION:FINAL_SUMMARY:END -->
