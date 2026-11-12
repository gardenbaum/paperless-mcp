---
id: PLMCP-21
title: Enforce the mutation-score gate
status: Done
assignee:
  - '@claude'
created_date: '2026-11-09T19:25:00Z'
updated_date: '2026-11-12T19:49:00Z'
labels: []
milestone: m-2
dependencies:
  - PLMCP-14
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: chore
ordinal: 21000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
From M2 on, the mutation score is a gate, not a report (design §9.2).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A mutation score of at least 80 % on services/ and paperless/ is enforced by the weekly and on-demand runs
- [x] #2 Every surviving mutant is either killed by a new test, turned into a Backlog task, or documented as equivalent
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [x] #1 Tests written first (red, then green) at every test level the change touches
- [x] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Run just mutation on the full scope to get the baseline survivors.
2. Kill each survivor with a new meaningful test at the right level (adapter/service/contract-on-fake, fakes not mocks); equivalents are justified one by one in a reviewed machine-readable file the score step reads; larger gaps become Backlog tasks.
3. Gate: MUTATION_GATE constant (80.0) in scripts/scheduled_jobs.py; mutation-score exits non-zero below it (tests red first), summary still printed; formula stays mutmut's (killed+timeout over total-skipped); equivalents file handled explicitly.
4. justfile mutation recipe and mutation.yml fail below the gate after publishing the summary and artifact; update docs; just check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Baseline (just mutation, full scope): 2079 mutants, 1898 killed, 2 timeout, 179 survived = 91.4 %. After: 2036 killed, 2 timeout, 41 survived = 98.0 % raw; 41 reviewed equivalents left out of the denominator = 100.0 % (2038/2038). Run time about 4 to 4.5 minutes locally (665 mutants at PLMCP-14, 2079 now).

Survivors: 138 killed by new tests (tests/services, tests/adapter: exact client-facing wording of errors and hints and log lines, date-range, error-length, page-cap, retry-budget and backoff-doubling boundaries, every name filter of the search, form data and files an upload sends, kind-specific fields, document types in suggestions, task wait edge cases); 41 equivalent; 0 turned into Backlog tasks (no larger gap found). The 2 timeouts (InstanceService.probe asyncio.timeout(None), pagination.collect 'or True' loop) are killed by mutmut's own timeout and counted as killed by its formula.

Gate (AC1): MUTATION_GATE = 80.0 is the single constant, in scripts/scheduled_jobs.py (--gate overrides it for tests only). 'mutation-score' prints the Markdown summary and then exits 3 (GATE_FAILED_EXIT_CODE; 1 stays 'job could not run', 10 stays drift) below the gate. just mutation pipes it through tee under pipefail, so the summary file is written and the recipe fails; mutation.yml publishes the summary and the artifact with if: !cancelled() after the failed step. Formula stays mutmut's badge formula, (killed + timeout) / (total - skipped), with one addition: survivors reviewed as equivalent leave the denominator like skipped ones. Suspicious, no_tests, segfault and interrupted mutants stay in the denominator and so count against the score; timeouts count as killed. No score (nothing tested) fails the gate. Red first: 22 of the new tests in tests/quality failed against the old script, green after.

Equivalents (AC2): mutation-equivalents.toml, one [[equivalent]] (mutant id, reason) per mutant, read by the score step together with 'mutmut results'. Only a mutant that still survives is left out; an entry that no longer survives (killed, or renumbered when its function changes) is reported as stale in the summary and not counted, so a renumbering cannot silently hide a new survivor, and does not fail the run either. mutmut has no per-mutant skip (only do_not_mutate by file/function glob and 'pragma: no mutate', and inline pragmas are banned), hence the file. The 41, by reason: zip() strict= on equal-length inputs (6: DocumentService._ids x3, .get x3); typing.cast ignores its first argument (15: _linked_titles x3, _fields x3, errors._messages x6, errors._details x3); HTTP header names are case-insensitive in httpx2.Headers (13: documents._file x4, client._build x2, client._retry_delay Retry-After x2, instance.get_overview x4, plus the unparsable 'XXXX' default of Retry-After x1); httpx2.Request upper-cases the method (3: client.download, client.get, instance.get_overview 'get'); stream=None is falsy like False (1: client.request); pagination.collect initial count never read since cap >= 1 guarantees one assignment (2); disposition._plain len >= 1 (1: a lone quote gives the empty name either way).

Not verifiable locally: the workflow on GitHub (actionlint is not installed here; the YAML parses). Flakiness: score and survivor set were identical in the second and third full runs apart from the intended kills; mutant ids are numbered per function, so editing a function renumbers its equivalents and the summary lists them as stale until updated.

Review round 1: (I1) equivalents are fingerprinted with the function_hash from mutants/mutmut-stats.json; an id that survives under a different function source is stale and not excluded (--hashes; proven live: editing PaperlessClient made the 3 _retry_delay entries stale and the run exited 4 until re-reviewed). (I2) stale entries print on stderr and in the summary and exit 4 (3 wins over 4); recipe and workflow fail with it. (I3) decision-13 'Mutation-score gate rules' (accepted, supplements decision-8, which is untouched); gate tests carry its marker. M4 PaperlessClient has a jitter seam (random_wait default); global random.seed removed. M5 real message asserted, date-range test asserts titles. M6 'user: null' note test reworded as defensive (schema says never null); streaming moved to tests/adapter/conftest.py. M7 'Other (not checked, type check)' row so the table sums to Total; formula names them. M8 --gate dropped. M9 publish step if: always(); missing or invalid mutmut data gives a clear error (exit 1). Final full run: 2081 mutants, 2038 killed, 2 timeout, 41 survived (all equivalent) = 98.0 % raw, 100.0 % with equivalents.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The mutation score on services/ and paperless/ is now a gate: the weekly workflow and just mutation fail (exit 3) below MUTATION_GATE = 80 % while still publishing the summary. Formula and rules are recorded in new decision-13 (supplements decision-8): (killed + timeout) / (total - skipped - confirmed equivalents); suspicious, no-tests, segfault, interrupted and not-checked mutants count against. Reviewed equivalents live in mutation-equivalents.toml, each with a reason and the function hash from mutmut-stats.json; an entry whose function changed is stale, is not excluded and fails the run (exit 4) until re-reviewed. All 179 survivors handled: 138 killed by new adapter and service tests, 41 documented as equivalent (zip lengths, typing.cast, header-name and method case, never-read initial values), none needed a follow-up task. Score 98.0 % raw, 100 % with equivalents (2081 mutants). PaperlessClient gained a jitter seam for a deterministic backoff test. Verified: just check 737 passed, 100 % diff coverage; full just mutation run. Independent review: 3 Important and 6 Minor fixed. DoD #4 is checked by the merge itself.
<!-- SECTION:FINAL_SUMMARY:END -->
