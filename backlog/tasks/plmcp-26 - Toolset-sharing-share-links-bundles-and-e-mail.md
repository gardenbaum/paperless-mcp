---
id: PLMCP-26
title: 'Toolset sharing: share links, bundles and e-mail'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-09T19:26:00Z'
updated_date: '2026-11-13T21:18:00Z'
labels: []
milestone: m-3
dependencies:
  - PLMCP-17
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 26000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Sharing documents with others is outward-facing: links expose documents and e-mails leave the system, so these tools are explicit about what they publish (design §6).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Share links list, create (with expiry) and delete; share-link bundles list, create, rebuild and delete
- [ ] #2 email_documents sends documents by e-mail and requires confirm; its annotations mark it open-world
- [ ] #3 Created links are returned as full URLs based on the public URL
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
1. Probe live 3.4.2 (done, see notes). 2. Domain: domain/sharing.py (ShareLink, ShareBundle, outputs) + SharingReader/SharingWriter ports. 3. Adapter paperless/sharing.py with schema-guarded adapter tests (red first). 4. FakePaperless state+methods, contract suite on fake and live (ports wired in contract conftest, harness, server Ports, coverage test). 5. services/sharing.py (SharingService: public URLs from public_url, expiry as days, confirm for deletes and email naming recipients and documents). 6. tools/sharing.py: 8 opt-in tools, annotations (email and link/bundle creation open-world), TOOL_TOOLSETS, TOOLSETS, Services, server wiring, import-linter contract, README, surface snapshot. 7. E2E on seeded data (links, bundles, refusal/validation paths of email). 8. Gates: just check (100% diff coverage), just mutation, flock just e2e.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Live probe on 3.4.2 (shared stack):
- Share URL: Paperless forms {public url}/share/{slug}; GET on that URL with no credentials answers 200 with the PDF (links) or application/zip (bundles). This server's web links use PAPERLESS_PUBLIC_URL or else PAPERLESS_URL (server.py web_url), so created links are web_url + /share/ + slug (AC #3).
- Links: POST /api/share_links/ {document, expiration (date-time or null), file_version archive|original}. Defaults: no expiry, archive. A past expiration is accepted (201) by Paperless; this server refuses it. Missing document -> 400 on field document. Delete missing -> 404.
- Bundles: POST /api/share_link_bundles/ {document_ids, expiration_days >= 1, file_version}; empty ids -> 400; unknown id -> 400 'Documents not found'. Response is status pending; build finishes in about 1s on text docs (status ready, size_bytes, built_at). expiration is set from expiration_days at creation. documents come back sorted by id. POST rebuild with the request body or with none answers 200 with status pending and then rebuilds to ready (a ready bundle can be rebuilt; failed ones are the use case, last_error carries the reason).
- E-mail: POST /api/documents/email/ {documents, addresses (comma separated), subject, message, use_archive_version}. Validation (bad address, empty docs, blank subject, unknown doc) -> 400 with field messages. On the E2E stack (compose.yaml sets no PAPERLESS_EMAIL_HOST, ui_settings email_enabled false) a valid request answers 500 'Error emailing documents, check logs'. So the live send cannot succeed there: live contract/E2E cover validation and the unconfigured-mail refusal; the send itself is covered by the fake and adapter tests. No SMTP service is added to compose.

Design decisions (PLMCP-26):
- Eight opt-in tools in toolset sharing: list_share_links, create_share_link, delete_share_link, list_share_bundles, create_share_bundle, rebuild_share_bundle, delete_share_bundle, email_documents. Default profile untouched (18 tools with AI; budget test unchanged).
- AC #3: share URL = web_url + /share/ + slug, where web_url is PAPERLESS_PUBLIC_URL or else PAPERLESS_URL (the value server.py already gives every service for document links). Verified live: GET {base}/share/{slug} serves the PDF / ZIP without credentials.
- AC #2: email_documents requires confirm; annotations: open_world true, destructive true (an e-mail cannot be recalled; this also makes the registry test demand the confirm parameter), idempotent false. The refusal names recipients and documents ('title' (id N)) and whether archive or original files go. create_share_link and create_share_bundle are open_world too (they publish) but need no confirm: the call is the request; delete_share_link and delete_share_bundle are destructive with confirm (decision-6, decision-15). rebuild is non-destructive, idempotent, closed world (same archive, same URL).
- Expiry: tools take expires_in_days (1..3650) for links and bundles alike; the service turns it into a date for links (the clock is injected) because Paperless takes a date-time for links and days for bundles. Paperless accepts a past link expiry (201); the range check keeps this server from creating a dead link. file_version archive|original is passed through (default archive, as Paperless).
- Bundles: create and rebuild answer status pending and finish in about a second for text documents; rebuild works on a ready bundle and ignores its body, but the OpenAPI schema requires one, so the adapter reads the bundle and sends its own document_ids (keeps the schema guard strict, no exemption added). A failed build exposes last_error (free-form JSON in the schema; shown as one bounded line).
- E-mail failure: Paperless answers 500 when it cannot send. The adapter maps a 500 or other transient status on that POST (never retried, so no double send) to FeatureUnavailable with a hint naming PAPERLESS_EMAIL_HOST; timeouts and unreachable stay as they are. Live E2E stack has no SMTP (no PAPERLESS_EMAIL_HOST in tests/e2e/compose.yaml, ui_settings email_enabled false): the live contract and E2E tests cover validation (400 per field) and the 'could not send' refusal; the successful send is covered by the fake (state.outbox, email_enabled switch) and the adapter tests. No SMTP service was added to the compose file.
- Fake: tests/fakes/sharing.py (functions; paperless.py only delegates, to keep it from growing). The fake has no mail server by default, like the stack, so the same contract test runs on both backends.
- Import-linter: paperless_mcp.tools.sharing added to the toolset independence contract. Surface snapshot regenerated deliberately for the all and read-only profiles only (new tools; default profile snapshot unchanged).

Live finding: despite the docs ('only document owners can create share links'), 3.4.2 lets a user with add_sharelink and only a view grant on a document create a link (and a bundle); a document the user cannot see is a 400 (document / document_ids not found). Fake, port docs, tool descriptions and contract tests follow the live behaviour. Bundle expiry from expiration_days counts from just before 'created' (2 days 23:59:59.99 for 3 days), so the contract test allows a minute.

Gates so far: just check green (2487 tests, total coverage 99.96 %, diff coverage 100 %); just mutation 100.0 % (8423 killed, 4 timeout, 0 survived; 119 reviewed equivalents, 14 of them new for sharing: mutmut's method-case and retry=None/True/omitted variants on POST/DELETE calls, each with its reason in mutation-equivalents.toml). Tests not written red first: tool-level tests (tests/tools/test_sharing.py) and the fake-side contract run were written after the code they exercise (the live contract run was the red check for the fake); the message snapshot and boundary tests were added after the first mutation run to kill surviving mutants.

Full just e2e: 435 passed, 6 skipped, 1 failed (test_more_like_finds_other_documents[live], the known PLMCP-40 failure, not worked around); all sharing contract (live) and E2E tests pass.
<!-- SECTION:NOTES:END -->
