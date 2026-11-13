---
id: PLMCP-40
title: Make the live more_like contract test reliable
status: To Do
assignee: []
created_date: '2026-11-13T16:56:00Z'
labels: []
milestone: m-3
dependencies: []
type: bug
ordinal: 40000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
tests/contract/test_document_reader.py::test_more_like_finds_other_documents[live] returned no hits at all on a fresh CI stack (gardenbaum/paperless-mcp PR #32, e2e run of d2de355; the rerun passed) and fails on the shared local stack, whose Whoosh index is stale. The PR did not touch the index, the seed or this test. Likely cause: the index entry of the seeded private document is not there or not committed yet when the test runs, for example because the contract bulk tests just before it trigger asynchronous index updates. Find the root cause on Paperless 3.4.2 (documents/index.py more_like, the index update tasks) and make the test wait for what it really depends on, or fix the seeding. Do not skip, quarantine or retry the test blindly.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The root cause of the empty more_like result is found and written in the task notes
- [ ] #2 The test waits for or establishes the index state it depends on, without a blind retry
- [ ] #3 The test passes on a fresh stack and on a stack after the full contract suite (bulk tests included)
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
