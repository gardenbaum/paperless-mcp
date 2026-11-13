---
id: PLMCP-25
title: 'Toolset views: saved views and run_saved_view'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-09T19:26:00Z'
updated_date: '2026-11-13T10:15:00Z'
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

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Probe 3.4.2 live: saved_views CRUD wire format/permissions and the document filters each rule type maps to (frontend FILTER_RULE_TYPES from the 3.4.2 main.js).
2. Domain: domain/views.py (SavedView, FilterRule, rule type table from RuleTypeEnum, ViewValues); DocumentQuery extended with the filters rules need; ports ViewReader (list/get) and ViewWriter (create/update/delete).
3. Adapter paperless/views.py + params() for new filters, schema-guarded adapter tests (red first).
4. FakePaperless: saved views with rules, new document filters; contract tests (fake+live) for ports and filters.
5. Services: filter_rules.translate (all 51 rule types translated or rejected with the rule type named; test lists every type), ViewService (list/get/create/update/delete with confirm, run via DocumentService.search for identical rows/paging).
6. Tools: views toolset (list_saved_views, get_saved_view, create_saved_view, update_saved_view, delete_saved_view, run_saved_view), registry/annotations/surface snapshot, server wiring, opt-in only.
7. E2E on seeded e2e-view; gates just check, just mutation, flock just e2e.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Live probe of 3.4.2 (before coding): saved view list/get rows carry id,name,icon,sort_field,sort_reverse,filter_rules,page_size,display_mode,display_fields,owner,user_can_change (no permissions without full_perms). Create: duplicate name for the same owner is 400 {error: ...}; PATCH to a duplicate name is ACCEPTED (200), so the service checks it itself. filter_rules is required on create ('This field is required.'), [] is valid; unknown rule_type 99 is 400; value over 255 chars 400; omitted value stored null; sort_field is not validated; page_size 0 is 400. PATCH replaces filter_rules as a whole. Missing view: 404 on get and delete. A user with only view_savedview sees only own/shared views (hidden one: GET 404), PATCH/DELETE/POST are 403 (model permission first); a view shared view-only lists with user_can_change false; reader without view_savedview gets 403 everywhere. Filter semantics probed on /api/documents/: correspondent__isnull, *__id__in, *__id__none (null documents included in none), owner__id__none includes unowned, owner__isnull, is_tagged, archive_serial_number(__gt/__lt/__isnull), created__year/month/day, created__date__lt/gt strict, modified__date__lt/gt, mime_type is icontains, has_duplicates, custom_fields__id__all/in/none, has_custom_fields, custom_fields__icontains, content__icontains, title_search, text, text+query combinable, text+title_search is 400 'Specify only one of text, title_search, query, or more_like_id.'. Rule table taken from 3.4.2 frontend main.js FILTER_RULE_TYPES (51 ids 0..50). Frontend maps rules 0 and 48 to title_search and 19 and 49 to text (not title__icontains / title_content).

Progress: DocumentQuery extended with 37 filter fields (adapter params schema-guarded; shared_by__id is declared boolean in the schema but takes a user id, added as MISTYPED_QUERY_PARAMETERS in the guard with self-tests; saved_views responses lack required 'permissions' on 3.4.2 so four WRONG_RESPONSE_SCHEMAS entries were added). ViewReader/ViewWriter ports, adapter, FakePaperless (saved views, view filters in tests/fakes/view_filters.py) and contract tests (filters + views) green on fake and live 3.4.2. Tests were written before code at each layer (adapter params, guard, views adapter, contract), run red first.

Progress: services (filter_rules.translate: all 51 rule types translated, unknown/bad values/duplicate single-valued/uncombinable searches/unknown sort fields rejected naming the rule type; ViewService list/get/create/update/delete(confirm)/run), tools/views.py (6 tools, opt-in toolset), server wiring, README, surface snapshot regenerated deliberately (only the 'all' and 'read-only' profiles gain the six/three views tools; default profile unchanged, still 18 tools), E2E tests (tests/e2e/test_views.py: seeded e2e-view equals the equivalent search_documents, create/run/update/delete flow, a stored unrunnable rule). Found by the service test: DocumentService.search overwrote ids already in the query with the (empty) name-resolved ones, which would have made every view run unfiltered; fixed (given names win, otherwise the query's ids stay) with its own red-first test. Test-after-code (honest): the E2E tests were written after the code (they passed first run); the duplicate-rule message wording in filter_rules was adjusted together with its test.

Gates on the tree rebased onto f3709f0: just check exit 0 (100% diff coverage); just mutation exit 0, 100.0% with 7 new reviewed equivalents (4 typing.cast first argument, 3 lowercase HTTP method); full e2e (18m13s on a shared stack): 335 passed, 2 failed: test_more_like_finds_other_documents[live] (shared-stack index, not worked around) and test_stdio selects-tools, which was mine (toolsets=all read-only now includes the 3 read-only views tools): fixed and rerun green. New live tests (e2e views + contract views + filters, 29 tests) take about 19 s.
<!-- SECTION:NOTES:END -->
