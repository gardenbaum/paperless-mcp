---
id: PLMCP-13
title: get_task with bounded wait
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-09T19:25:00Z'
updated_date: '2026-11-12T17:55:00Z'
labels: []
milestone: m-1
dependencies:
  - PLMCP-8
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 13000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Uploads and PDF operations run asynchronously in Paperless. Clients need to follow a task to its result without polling by hand (design §6).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 get_task returns status, result message and the related document id when known
- [ ] #2 wait_seconds polls until the task finishes or the bounded maximum is reached, and reports which happened
- [ ] #3 An unknown task id returns NotFound
- [ ] #4 FakePaperless and the contract suite cover every new port method
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
1. Probe live 3.4.2 (done): GET /api/tasks/?task_id=<uuid> is the lookup (post_document returns the Celery UUID; /api/tasks/{id}/ takes the DB integer, not the UUID); an unknown id is 200 with count 0 (-> NotFound); a user without view_paperlesstask gets 403; status pending/started/success/failure/revoked; success carries related_document_ids and result_data.document_id; failure carries result_data.error_message plus a traceback that must never reach clients.
2. Domain: domain/tasks.py (TaskStatus, TaskReport, final states), TaskReader port (get_task).
3. Adapter paperless/tasks.py PaperlessTaskReader (schema-guarded tests first; 200 on list checked against schema).
4. FakePaperless: FakeTask state + get_task with the 403/empty semantics; contract tests (fake and live).
5. Service TaskService(reader, sleep, clock) bounded wait (injected sleeper/clock, no real sleeping), max 30 s, poll 2 s, reports outcome (not_requested/finished/timed_out) and waited seconds.
6. Tool get_task(task_id, wait_seconds) in core, ANNOTATIONS entry (read-only), wire in server.py; surface snapshot regenerated deliberately; e2e through the server with a real upload.
7. just check, flock just e2e.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Live-verified on Paperless 3.4.2: (1) lookup is GET /api/tasks/?task_id=<celery uuid> (post_document returns that UUID; /api/tasks/{id}/ takes the integer DB id, so it is not used). (2) An unknown task_id is 200 with count 0 (mapped to NotFound); GET /api/tasks/999999/ is 404. (3) Statuses seen: started, success, failure; success carries related_document_ids=[n] and result_data={document_id:n}; failure (broken PDF) has related_document_ids=[] and result_data={error_type,error_message,traceback}: only error_message is exposed (stripped), the traceback never leaves the adapter. A duplicate upload is NOT a failure on this stack. (4) A user without view_paperlesstask (the seeded e2e-reader) gets 403 on /api/tasks/ (PermissionDenied). Design: TaskReader.get_task port; TaskStatus/TaskReport domain models; TaskService(reader, sleep=, clock=) polls every 2 s, bound min(wait,30) s, last sleep cut to the remaining time; wait reports not_requested (wait_seconds 0), finished or timed_out plus waited_seconds. create_server gained sleep/clock kwargs (default asyncio.sleep/time.monotonic) so the tool-level wait is tested without sleeping. Test seams: FakeTask/FakeState.tasks, Backend.consume(succeed=) in the contract suite (live: real upload, document deleted again in the finaliser; Paperless keeps it in the trash, I did not empty the trash). Red->green: adapter, fake/contract (fake), service and tool tests were run red (ImportError / missing attribute) before the code; the live contract and e2e tests were written after the adapter existed, so their first run was green (first e2e attempt hit the /api/token/ 429). Deliberate changes: surface snapshot regenerated (only additions: get_task); tests/contract/test_coverage.py wiring assertion gained 'tasks': TaskReader; no gate, threshold or schema-guard exception was changed (the 200 of GET /api/tasks/ validates against the vendored schema). just check passes (100% coverage, diff coverage 100%); flock just e2e: 57 passed.
<!-- SECTION:NOTES:END -->
