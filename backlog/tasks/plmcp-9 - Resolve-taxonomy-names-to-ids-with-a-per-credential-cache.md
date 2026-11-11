---
id: PLMCP-9
title: Resolve taxonomy names to ids with a per-credential cache
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-09T19:25:00Z'
updated_date: '2026-11-11T20:00:00Z'
labels: []
milestone: m-1
dependencies:
  - PLMCP-8
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 9000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Paperless wants ids; users and LLMs think in names. None of the reference servers does this correctly: one resolves only page 1, another picks the first fuzzy match. Name resolution is this server's main differentiator (design §5.4).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Resolves tags, correspondents, document types, storage paths, custom fields, saved views, users and groups
- [ ] #2 Numeric input is an id; otherwise exact match, then case- and accent-insensitive match; more than one match raises Ambiguous with the candidates; no match raises NotFound with the closest suggestions; a fuzzy match is never picked automatically (property tests with hypothesis)
- [ ] #3 The same name in different kinds (tag Tax vs document type Tax) resolves independently per kind
- [ ] #4 Objects the credential cannot see are treated as not existing, never leaked through suggestions
- [ ] #5 The cache loads all pages, expires after a TTL, is keyed by credential fingerprint and is invalidated by the server's own writes
- [ ] #6 list_taxonomy(kind, name_contains) and paperless://taxonomy/{kind} return compact rows (id, name, document count; colour and parent for tags; data type and select options for custom fields)
- [ ] #7 FakePaperless and the contract suite cover every new port method
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
1. domain/taxonomy.py: TaxonomyKind (Literal of the 8 kinds) and TaxonomyItem (id, name, document_count; colour, parent for tags; data_type, select_options for custom fields; null elsewhere). domain/errors.py: Ambiguous. domain/ports.py: TaxonomyReader.list_taxonomy(kind) -> every visible object over all pages; PermissionDenied when the user lacks the global view permission of the kind (verified live: e2e-reader gets 403 on saved_views, users, groups).
2. paperless/taxonomy.py: PaperlessTaxonomyReader on /api/{tags,correspondents,document_types,storage_paths,custom_fields,saved_views,users,groups}/ via collect(); users map username to name and drop e-mail etc.; more than MAX_ITEMS raises (shortcut, documented). Schema-guarded adapter tests.
3. FakePaperless.list_taxonomy with Paperless's rules (global view_<model> permission, owner/viewer visibility for owned kinds, document_count over visible documents); seed gains custom fields, a saved view and a group in the fake and the live seed. Contract tests per kind for admin and reader.
4. services/taxonomy.py: pure pick(kind, items, value) (numeric = id; exact; case- and accent-insensitive; Ambiguous; NotFound with difflib suggestions from visible items only), property tests with hypothesis; TaxonomyResolver with a cache keyed (credential fingerprint, kind), TTL, invalidate(kind) for all credentials (called by writes from PLMCP-19), items(kind, name_contains), resolve(kind, value). Unit tests against FakePaperless.
5. tools/core.py: list_taxonomy(kind, name_contains) (capped at 100 rows with count and truncated) and resource template paperless://taxonomy/{kind} (application/json, domain errors as ResourceError). Annotation table entry, server wiring (Ports.taxonomy, Services.taxonomy, TAXONOMY_TTL = 300 s), harness, surface snapshot regenerated deliberately.
6. Carry-over from PLMCP-8: boundary re-raises ToolError before the catch-all, with a test.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented (plan steps 1-6). domain/taxonomy.py (TaxonomyKind, TaxonomyItem, TaxonomyListing), Ambiguous error, TaxonomyReader port; paperless/taxonomy.py adapter (users expose only id and username; >MAX_ITEMS of a kind raises, documented shortcut); services/taxonomy.py pick() + TaxonomyResolver (cache keyed by fingerprint and kind, TTL 300 s via server.TAXONOMY_TTL, invalidate(kind) for every credential); tools: list_taxonomy (100-row cap, count, truncated) and resource template paperless://taxonomy/{kind}; boundary re-raises ToolError (PLMCP-8 carry-over).
Verified live before writing the fake: e2e-reader gets 403 on /api/saved_views/, /api/users/, /api/groups/ (no view_savedview/view_user/view_group), so list_taxonomy raises PermissionDenied for those kinds; Paperless lists by name. Seed (fake and live) gains a group e2e-group and an admin-owned saved view e2e-view; the fake gains custom fields with select options and document links.
Deliberate surface change: tools snapshot adds list_taxonomy; new snapshot of the resource templates (decision-4 asks for the full surface). No gate, threshold or decision changed.
TDD: service, contract and tool tests were red before the code; the adapter test was written first but not run red separately.
Verification: just check 236 passed, coverage 100 %, diff coverage 100 %; just e2e 25 passed (contract suite live incl. 15 TaxonomyReader cases).

Task review (opus): no Critical. Important fixed: property test_a_match_is_never_a_guess compared against the unstripped query (flaky; counterexample pool ['a'], query ' a' now pinned with @example). Minors fixed: a bool is a name, not an id; a digit-only name's NotFound hint names its id. Minors accepted, not fixed: NFKD folding misses non-decomposing letters (ł, ø, đ; fails safe as NotFound); stored names are not stripped; unknown kind on the resource gives FastMCP's generic error; contract suite does not pin document_count visibility or ordering; rows carry null fields (no exclude_none); nothing calls invalidate yet. Carry to PLMCP-16/19: document and taxonomy writes must call TaxonomyResolver.invalidate (document writes change document_count). just check 237 passed, 100 %.
<!-- SECTION:NOTES:END -->
