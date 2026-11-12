---
id: PLMCP-14
title: 'Scheduled quality jobs: mutation testing, E2E against latest, drift check'
status: Done
assignee:
  - '@claude'
created_date: '2026-11-09T19:25:00Z'
updated_date: '2026-11-12T16:49:00Z'
labels: []
milestone: m-1
dependencies:
  - PLMCP-8
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: chore
ordinal: 14000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Weekly jobs catch weak tests and upstream drift. Tests written by AI often only restate the code; mutation testing measures that. Paperless releases monthly (design §9.2, §9.5, §14).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 mutmut is configured for services/ and paperless/; a weekly workflow publishes the mutation score (report-only until PLMCP-21)
- [x] #2 A weekly workflow runs the E2E suite against the latest paperless-ngx 3.x image
- [x] #3 A weekly drift check compares the vendored Paperless schema and docs with the latest 3.x release and opens a GitHub issue when they differ
- [x] #4 Every scheduled job can also be started on demand (workflow_dispatch and a just recipe)
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
1. Add scripts/paperless_release.py (pure, tested): latest stable 3.x tag from git ls-remote output, drift comparison of two vendored dirs (ignoring UPSTREAM commit/digest), issue dedupe via injected gh runner, mutation score from mutmut stats. Tests first under tests/scripts (red then green); scripts/ added to coverage source, pythonpath and basedpyright.
2. mutmut as dev dep (uv add --dev), [tool.mutmut] for services/ and paperless/, non-e2e tests only.
3. E2E override: tests/e2e/compose.latest.yaml (image tag from PAPERLESS_E2E_VERSION), own compose project + port so it never touches the shared stack; e2e tests read expected version from PAPERLESS_E2E_VERSION.
4. docs-sync-paperless gets PAPERLESS_DOCS_DIR output override. Recipes: mutation, e2e-latest, drift-check.
5. Workflows: mutation.yml, e2e-latest.yml, drift.yml (weekly cron + workflow_dispatch, SHA-pinned, least privilege; drift has issues: write, report-only mutation summary + artifact).
6. Validate with actionlint, local mutmut proof on one module, just check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Design choices: (1) mutmut 3.8.1 (supports Python 3.14), configured in [tool.mutmut]: source_paths = services/ + paperless/, also_copy for src/, docs/vendor/paperless/ (schema guard) and backlog/decisions/; --no-cov and --ignore=tests/quality; e2e stays deselected via addopts. Full local run: 665 mutants in ~2 min, score 87.4 % (579 killed, 2 timeout, 84 survived), so the full scope is the local proof. Report-only: no threshold; PLMCP-21 gates. (2) Logic in scripts/scheduled_jobs.py (latest stable 3.x tag from git ls-remote, drift comparison ignoring UPSTREAM commit/digest, one-issue dedupe via label paperless-drift, injected runner for gh, mutation score = mutmut badge formula). Tests first in tests/quality (red: 23 failed against a stub, then green). scripts/ added to coverage source, pytest pythonpath, basedpyright include; one reviewed coverage exclusion for 'if __name__ == "__main__":'; ruff INP001 ignored for scripts/** (standalone scripts). No gate weakened. (3) E2E latest: overlay tests/e2e/compose.latest.yaml (image tag from PAPERLESS_E2E_VERSION) with own compose project and port 8011, so the pinned stack is untouched; e2e tests read the expected version from PAPERLESS_E2E_VERSION (default 3.4.2). (4) docs-sync-paperless got PAPERLESS_DOCS_DIR so drift-check regenerates into a temp dir; verified locally: just drift-check -> 'no drift' vs 3.4.2. Drift issue: opened or refreshed, never duplicated. (5) Workflows mutation.yml, e2e-latest.yml, drift.yml: cron + workflow_dispatch, SHA pins as ci.yml, contents: read (drift adds issues: write), actionlint clean. Not verifiable locally: the workflows on GitHub, gh issue creation, e2e-latest against a real stack (shared stack in use).

Review round 1: contract suite expects the version from Backend.expected_version (fake: VERSION, live: PAPERLESS_E2E_VERSION); no other live-path 3.4.2 assertions remain (the rest are fake/recorded-adapter tests). Drift lifecycle: refresh edits and comments, drift-free run closes the issue (new recipe drift-issue, separate step so GH_TOKEN only reaches that step; issues: write at job level). Latest-version resolution skips tags whose GHCR image is missing (docker manifest inspect, falls back to next lower tag); e2e-latest version input validated via 'release' subcommand. mutation recipe uses positional arguments and notes that a selection aggregates earlier results. persist-credentials: false on the three new workflows. Exit code 10 cross-referenced in justfile and script. main() takes err; fmt: skip removed; boundary test at 50 items; LOGS_HINT follows e2e-latest; log upload on failure or cancel. Config notes: ruff per-file ignore INP001 for scripts/** (standalone scripts, no __init__.py) was added in the first commit; basedpyright scope widened from 'src and tests' to include scripts, so decision-8's text 'src and tests' is out of date (widened, not weakened; decision not edited).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Three weekly jobs, each also on demand (workflow_dispatch and a just recipe): mutation.yml / just mutation runs mutmut 3.8.1 on services/ and paperless/ and publishes the score as job summary and artifact (report-only; PLMCP-21 gates at 80 %); e2e-latest.yml / just e2e-latest runs the E2E suite against the highest 3.x release with a published GHCR image, on its own compose project and port; drift.yml / just drift-check regenerates the Paperless schema and docs for the latest release into a temp dir and compares them with docs/vendor/paperless, opening or refreshing (with a comment) one labelled issue on drift and closing it when drift is gone. Logic lives in scripts/scheduled_jobs.py with fake-runner tests; the contract suite now expects the backend's own version. Verified: just check 274 passed, 100 % coverage; local mutmut run 665 mutants, 87.4 %; just drift-check reports no drift vs 3.4.2; actionlint clean. Independent review: 1 Critical, 2 Important, 8 Minor, all fixed in round 1. Not verifiable before merge: the workflows on GitHub (cron, gh issue creation) and e2e-latest against a newer image (latest is still 3.4.2). Config: ruff INP001 ignored for scripts/**, basedpyright scope widened to scripts (decision-8 text 'src and tests' now outdated), one exclude_also pattern for __main__ guards. DoD 4 is checked by the merge itself.
<!-- SECTION:FINAL_SUMMARY:END -->
