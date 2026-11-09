---
id: PLMCP-2
title: Create public GitHub repository with CI and branch protection
status: Done
assignee:
  - '@claude'
created_date: '2026-11-09T19:25:00Z'
updated_date: '2026-11-09T20:24:00Z'
labels: []
milestone: m-0
dependencies:
  - PLMCP-1
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: chore
ordinal: 2000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The design relies on CI blocking violations. That only works with required checks on a protected main, which GitHub Free offers for public repositories (with unlimited Actions minutes). The user chose a public repository. Creating it is outward-facing: confirm with the user right before running gh repo create.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Public repository gardenbaum/paperless-mcp exists and local main is pushed, after explicit user confirmation
- [x] #2 Workflow check runs the just check gates on Python 3.14 and 3.15
- [x] #3 Workflow audit runs pip-audit (with the ignore file) and gitleaks
- [x] #4 main is protected: pull request required, required checks check (3.14, 3.15) and audit, no force pushes, no direct pushes
- [x] #5 Third-party actions are pinned to full commit SHAs; Renovate is configured for Python dependencies, GitHub Actions and pinned container images
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [x] #1 Tests written first (red, then green) at every test level the change touches
- [x] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [x] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Create public repo, push main. 2. Bootstrap PR #1 for plmcp-1-scaffold, squash-merge. 3. Rebase plmcp-2 onto main. 4. Add .github/workflows/ci.yml (check 3.14/3.15 running just check; audit job with pip-audit + gitleaks), renovate.json; SHA-pinned actions. 5. PR #2, iterate until green; leave unmerged. Protection payload in report.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Task review (sonnet): approved, no Critical/Important. Branch protection applied by controller before merging PR #2 (verified via API: required checks check (3.14), check (3.15), audit; strict; enforce_admins; linear history; no force pushes; conversation resolution).
Owner action needed: install the Renovate GitHub app on the repo.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Created public repo gardenbaum/paperless-mcp (SSH remote), bootstrapped PLMCP-1 via PR #1, added GitHub Actions CI (check matrix on Python 3.14 + 3.15 running the same just recipes as `just check`, audit job with pip-audit + full-history gitleaks), all actions SHA-pinned, least-privilege permissions, Renovate config (uv, actions, pre-commit, docker, just pin). main is protected with required checks and PR-only merges.
Follow-ups (minor): CI step list duplicates the justfile check recipe; go-version stable unpinned; gitleaks version duplicated in pre-commit (Renovate bumps only rev); audit not scheduled (PLMCP-14); Renovate app must be installed by the owner.
<!-- SECTION:FINAL_SUMMARY:END -->
