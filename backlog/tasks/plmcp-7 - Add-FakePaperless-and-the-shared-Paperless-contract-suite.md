---
id: PLMCP-7
title: Add FakePaperless and the shared Paperless contract suite
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-09T19:25:00Z'
updated_date: '2026-11-09T22:04:00Z'
labels: []
milestone: m-0
dependencies:
  - PLMCP-5
  - PLMCP-6
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 7000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Services and tools are tested against an in-memory fake instead of mocks. To keep the fake honest, one contract suite runs against the fake in the unit run and against the real adapter plus live Paperless in E2E (design §9.1). Every later task that adds port methods extends both.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 FakePaperless implements the ports defined so far with realistic pagination, permission behaviour (403/404 for objects the credential cannot see) and PATCH semantics
- [ ] #2 FakePaperless rejects write calls unless the test opts in (write guard)
- [ ] #3 The contract suite is parametrised over the fake and the live backend; just check runs it against the fake, just e2e against Paperless 3.4.2
- [ ] #4 A test fails when a port method has no contract test
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
1. Verify on the live 3.4.2 stack: /api/statistics/ (any user, counts scoped to visible objects unless superuser or view_global_statistics), /api/status/ (superuser, staff or view_system_monitoring; otherwise 403 text/html), /api/ui_settings/ (view_uisettings; current user, ai_enabled), versions from the X-Version / X-Api-Version headers. Exclude /api/remote_version/ (update check, not instance state) and /api/profile/ (returns the auth token).
2. Domain: frozen pydantic models in domain/instance.py (InstanceOverview, InstanceStatistics, SystemStatus, ComponentStatus, CurrentUser) and the InstanceReader Protocol in domain/ports.py (single home of all Paperless ports). Restricted user: system_status None on 403, current_user/ai_enabled None on 403.
3. Adapter paperless/instance.py (PaperlessInstanceReader on PaperlessClient), wire models private, mapping wire->domain; extract the JSON parse step of PaperlessClient.call so header-reading calls reuse it. Schema guard: documented deviations for responses the 3.4.2 schema describes wrongly (status 200 and 403, ui_settings 200), compensated by the live contract suite. Adapter tests with bodies recorded from the stack.
4. tests/fakes/paperless.py: FakeState (users, documents, taxonomy with owner/viewers), FakePaperless bound to one user, seeded() mirroring the E2E seed, write guard (@write decorator, allow_writes opt-in, WriteNotAllowedError), proven with a test-only write method.
5. tests/contract/: backend fixture parametrised [fake, live(e2e marker)] with admin and reader; contract tests marked pytest.mark.contract('Port.method'); meta-tests: every port method has a contract test, every marker names a port method, every *Writer method is guarded in the fake.
6. E2E seed: shared taxonomy created with owner null (default owner is the creator, verified), an inbox tag, reader gets view_uisettings; e2e asserts reader sees shared taxonomy but not the private tag. Move the stack fixture to tests/conftest.py for the contract suite.
7. TDD red/green per slice; just check, just e2e, just e2e-down; commit.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented InstanceReader (domain/ports.py, domain/instance.py), PaperlessInstanceReader (paperless/instance.py), FakePaperless with write guard (tests/fakes/paperless.py) and the contract suite (tests/contract/) incl. coverage meta-tests. Verified on 3.4.2: statistics are scoped to visible objects for users without view_global_statistics; /api/status/ is 403 text/html for non-staff without view_system_monitoring (overview: system_status None); /api/ui_settings/ needs view_uisettings (otherwise current_user and ai_enabled None); versions from X-Version/X-Api-Version. Schema guard: deliberate, documented deviations for GET /api/status/ 200 and 403 and GET /api/ui_settings/ 200, whose 3.4.2 schema is wrong; the live contract suite covers the real shapes. E2E seed: shared taxonomy now owner null (Paperless default owner is the creator, which hid it from the reader), new shared inbox tag e2e-inbox on the public document, reader gets view_uisettings; tokens fetched once per session (/api/token/ is throttled). Not built yet (no port needs them): pagination and PATCH behaviour of the fake arrive with the first list/patch port.

Fix round 1 (R16): contract tests use the SEEDED_OBJECTS manifest (e2e/helpers.py) as lower bounds instead of exact counts, the fake seed is checked exactly against it; live isolation rule in tests/contract/conftest.py. Coverage guard now runs on items pytest collects (pytester, enabled via -p pytester): a method counts only with a contract-marked test taking backend with both fake and live params. Backend is now explicit wiring (Ports dataclass, one field per port; fake_ports/live_ports) instead of a multiple-inheritance Adapter; meta-tests check every port is wired and implemented by fake and adapter. ports.py states the *Writer rule.
<!-- SECTION:NOTES:END -->
