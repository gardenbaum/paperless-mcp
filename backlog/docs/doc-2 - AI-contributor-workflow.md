---
id: doc-2
title: AI contributor workflow
type: guide
created_date: '2026-11-12T16:23:00Z'
updated_date: '2026-11-12T16:23:00Z'
---
# AI contributor workflow

How AI sessions (local or Claude Code cloud) implement Backlog tasks in this repository. Repository rules in CLAUDE.md and the approved design (doc-1) take precedence.

## Roles and per-task flow

A controller session runs the loop and delegates; it keeps its own context small.

1. Controller: pick the next task whose dependencies are merged; `git switch -c plmcp-N-<slug>` from an up-to-date main; set the task In Progress and assign it.
2. Implementer subagent (model sonnet, worktree isolation when tasks run in parallel): read `backlog instructions task-execution`, record the plan with `backlog task edit PLMCP-N --plan`, then strict TDD: every new test at every level (adapter, contract, service, tool, e2e) is run red before the code exists, then green. Append progress with `--append-notes`. `just check` and `just e2e` must pass.
3. Reviewer subagent (model opus, independent, sees only the diff, the task and the design): checks acceptance criteria, design, decisions and gates. Findings are Critical, Important or Minor; Critical and Important are fixed in up to three fix rounds; Minor findings that later tasks depend on are fixed now, the rest go to the task notes as deferred.
4. Controller: read `backlog instructions task-finalization`, check acceptance criteria with evidence, write the final summary, set status Done, commit on the branch. Leave a Definition-of-Done item unchecked when it is not true and say why.
5. Controller: push, open the PR, wait for the required checks and for the Codex review to complete, handle every review thread, squash-merge, delete the branch, update main.

## GitHub from Claude Code cloud sessions

- GraphQL is blocked: `gh pr` and `gh issue` do not work. Use REST through `gh api repos/gardenbaum/paperless-mcp/...`.
- Review threads, auto-merge and draft state use the CCR routes: `GET .../pulls/{n}/ccr/review_threads`, `POST .../pulls/{n}/ccr/comments/{comment_id}/resolve`, `PUT .../pulls/{n}/ccr/auto_merge`.
- Merge: `gh api -X PUT repos/gardenbaum/paperless-mcp/pulls/{n}/merge -f merge_method=squash`.
- Branch protection on main: required checks `check (3.14)`, `check (3.15)`, `audit`, `e2e`; branch must be up to date; enforced for admins; linear history; all conversations resolved. Never weaken a gate without a decision (CLAUDE.md).
- The Codex bot reviews every opened PR. Fix P1 findings before merging. P2/P3 findings are fixed in the PR unless they concern only test infrastructure or docs and would take long; then reply in the thread, create a follow-up task and resolve the thread.

## When something blocks

- CI runs stuck in "queued": do not debug Actions. Re-create the commit with a fresh SHA (`git commit --amend --no-edit`), push it on a new branch, open a new PR that references the old one, close the old one.
- A merge blocked for long: continue with tasks that do not depend on it, branched from main. A task that needs the blocked code is stacked on that PR's branch; CI runs only for PRs against main, so `just check` and `just e2e` in the session are the evidence until the base merges, then rebase onto main and retarget the PR.

## Parallel work

At most two tasks at a time, chosen for independent dependencies and little file overlap. Never run `just e2e` concurrently: there is one shared Paperless stack. Hotspots every new tool touches: `tools/core.py`, `tools/_shared.py` (ANNOTATIONS), `tests/tools/__snapshots__/test_surface.ambr`, `tests/fakes/paperless.py`, `domain/ports.py`, `server.py`. After each merge, rebase the other branch and regenerate snapshots deliberately (say so in the commit message).

## Standing rulings

- Python 3.15 stays a required check. An upstream warning that breaks 3.15 gets one narrowly scoped `filterwarnings` entry with a reason and a follow-up task to remove it.
- Registry and meta checks that could pass vacuously get a self-test with a synthetic violating provider.
- Every port added to the domain gets a FakePaperless implementation and contract tests on fake and live 3.4.2, with realistic pagination and PATCH semantics.
- Inline `# pragma` and `# type: ignore` are banned; coverage exclusions are reviewed `exclude_also` patterns in pyproject.toml.
- A gate, threshold, contract or accepted decision changes only through a new decision that supersedes it.
- Verification against the owner's own Paperless instance is the owner's job; automated tests use the E2E stack (3.4.2).
- The tool error boundary re-raises ToolError unchanged; any other exception is logged by type only and masked.
- Split FakePaperless into per-port mixins once it grows further.

## Global constraints (summary of doc-1)

- paperless-ngx 3.x only, API version 10; reference version 3.4.2. FastMCP 4.1.x. httpx2 is the only HTTP library.
- Python 3.14 baseline; CI also runs 3.15.
- Layers: entrypoints → server.py (composition root, the only module importing both tools/ and paperless/) → tools/ and paperless/ → services/ → domain/. domain imports nothing from the package and no I/O or MCP library; services import domain only; paperless imports domain, never fastmcp/mcp/services/tools; tools import services and domain, never httpx2/paperless; only paperless imports httpx2. Toolset modules in tools/ are independent.
- Every request sends `Accept: application/json; version=10` and the auth header of the current credential.
- Tools: snake_case verb-first names, title, LLM-oriented description, typed and described parameters, annotations from the central table, exactly one toolset tag, typed return model. Destructive tools take `confirm: bool = False`; no elicitation. Domain errors become ToolError with message and hint through one boundary.
- Token economy: default page size 25, max 100; content slices of 20 000 characters; aggregated fetches capped with `truncated`.
- Logging to stderr only; never document content, tokens or full query text at INFO or above.
- Tests: fakes over mocks, `unittest.mock` and patching banned; seams are constructor injection and the httpx2 transport. Adapter requests and responses are validated against the vendored OpenAPI schema. Branch coverage ≥ 95 % overall, 100 % on changed lines; mutation score ≥ 80 % on services/ and paperless/ (enforced from PLMCP-21).
- Everything in the repository is in English. Commit messages end with the Co-Authored-By trailer.
