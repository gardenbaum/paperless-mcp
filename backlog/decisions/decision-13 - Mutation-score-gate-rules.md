---
id: decision-13
title: Mutation-score gate rules
date: '2026-11-12T19:43:00Z'
status: accepted
---
## Context

Decision-8 set a mutation score of at least 80 % on `services/` and `paperless/` and left it report-only until PLMCP-21. Enforcing it needs rules that cannot be bent quietly: which mutants count how, what may be excluded, and what makes the weekly run fail. This decision supplements decision-8 (which is not changed) and records the rules PLMCP-21 implemented.

## Decision

- Threshold: `MUTATION_GATE = 80.0` percent, one constant in `scripts/scheduled_jobs.py`. The weekly workflow and `just mutation` fail below it, after the summary is printed and published.
- Formula, mutmut's badge formula: `(killed + timeout) / (total - skipped - equivalent)`. Timeouts count as killed (mutmut's own timeout stopped the mutant). Suspicious, no-tests, segfault, interrupted and not-checked (type check) mutants stay in the denominator and so count against the score. A run with nothing tested fails.
- Equivalent mutants are excluded only through `mutation-equivalents.toml`: one `[[equivalent]]` per mutant with `mutant` (mutmut id), `function_hash` (the hash of the mutated function from `mutants/mutmut-stats.json`) and a `reason` saying why no test can tell it from the original. A killable mutant gets a test, not an entry.
- Mutant ids are positional, so an entry counts only while the mutant still survives and its function still has the reviewed hash. Any other entry is stale: it is not counted, the score step names it in the summary and on stderr, and the run exits 4 until the entry is reviewed again.
- Exit codes of `scheduled_jobs.py mutation-score`: 0 passed; 1 could not run (missing or invalid data); 3 below the gate; 4 stale equivalents (3 wins if both). The threshold has no command-line override.
- Changing the threshold, the formula or these rules requires a new decision that supersedes this one.

## Consequences

- Editing a mutated function in `services/` or `paperless/` can make the weekly run red until its equivalents are reviewed: accepted, that is the point.
- A full run takes about five minutes; the gate runs weekly and on demand, not on every PR.
- Mutants that no test can kill are visible one by one in the repository, with reasons.

## Enforcement

- `tests/quality/test_scheduled_jobs.py` (marked `decision-13`): the gate constant, boundary at the gate, the formula and how each status counts, equivalent parsing, function-hash staleness, the summary, and exit codes 1, 3 and 4. They run in `just check`.
- `just mutation` and `.github/workflows/mutation.yml` run the score step with `--results`, `--equivalents` and `--hashes`, publish the summary even on failure, and fail with the script's exit code (CI: the weekly and on-demand run; manual review of the workflow, which cannot run in `just check`).
