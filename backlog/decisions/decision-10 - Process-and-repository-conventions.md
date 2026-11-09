---
id: decision-10
title: Process and repository conventions
date: '2026-11-09T20:44:00Z'
status: accepted
---
## Context

The project is public, written largely by AI agents and managed in Backlog.md. Without fixed process rules, work drifts into untracked side channels (an ADR folder, TODO files) and straight pushes to `main`.

## Decision

- Backlog.md only: the design is a Backlog document, decisions are Backlog decisions, the plan is Backlog tasks and milestones. No OpenSpec, no separate ADR folder. Task, draft, document, milestone and decision metadata change only through the `backlog` CLI.
- Decision-body exception (documented in CLAUDE.md and AGENTS.md): the CLI creates a decision and sets its title and status but cannot set its body, so a decision body (Context, Decision, Consequences, Enforcement) is written directly in the file. Frontmatter is never edited by hand. A change to an accepted decision is a new decision that supersedes it.
- Every accepted decision names its guard in an `## Enforcement` section; "manual review: reason" is allowed when no automation is possible.
- Everything in the repository is written in English.
- One branch and one pull request per task; `main` is protected and accepts a merge only with the required checks green: `check (3.14)`, `check (3.15)` and `audit` (the `e2e` check joins when PLMCP-5 lands).
- `just check` is the local gate and mirrors CI.

## Consequences

- Agents get one place to find the why (decisions) and the what (tasks).
- Tasks are not done until their PR is merged with green checks.
- The decision-body exception is the only direct edit of Backlog files.

## Enforcement

- `tests/test_decisions.py`: every accepted decision has a non-empty `## Enforcement` section; every `@pytest.mark.decision("decision-N")` marker references an existing decision; the checks are proven on synthetic input; decisions 1-10 exist and are accepted. The `decision(id)` marker is registered in `pyproject.toml` (strict markers).
- Branch protection settings of `main` (required checks above) and the workflow in `.github/workflows/ci.yml`.
- Manual review: Backlog-only process, English, the CLI-only frontmatter rule and one PR per task cannot be machine-checked; they are stated in CLAUDE.md and AGENTS.md and checked in PR review.
