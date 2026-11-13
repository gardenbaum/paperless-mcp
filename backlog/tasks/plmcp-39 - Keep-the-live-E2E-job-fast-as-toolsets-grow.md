---
id: PLMCP-39
title: Keep the live E2E job fast as toolsets grow
status: To Do
assignee: []
created_date: '2026-11-13T07:29:00Z'
labels:
  - ci
  - testing
dependencies: []
priority: medium
ordinal: 39000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The e2e CI job runs the live contract suite and the E2E tests against one paperless-ngx 3.4.2 stack. main took 11-12 min; PR runs adding one toolset (PLMCP-22 bulk, PLMCP-24 versions) exceeded the 20-minute job timeout, with worker-bound modules (document_writer, notes_and_trash, document_upload) about 2x slower than on main runs. The timeout was raised to 35 minutes as a stopgap. Find where the time goes and bring the job back well under 20 minutes without dropping live coverage.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Per-module timings of the e2e job are measured on CI and recorded in the notes, including why worker-bound modules vary 2x between runs
- [ ] #2 Consume-heavy live tests use plain text documents where OCR, archives, page counts or thumbnails are not what they test
- [ ] #3 The e2e job (or its split into parallel jobs with their own stacks, if the owner approves the required-check change) finishes in under 20 minutes on main
- [ ] #4 The job timeout is lowered back once the suite is fast again
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
