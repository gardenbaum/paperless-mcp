
<!-- BACKLOG.MD GUIDELINES START -->
<!-- backlog.md-instructions-version: 1.55.3 -->
<CRITICAL_INSTRUCTION>

## Backlog.md Workflow

This project uses Backlog.md for task and project management.

**At the beginning of each conversation in this project, run `backlog instructions overview` before answering or taking action. Re-read it only if you have not read it yet in the current conversation.**

Use the overview to decide whether to search, read, create, or update Backlog tasks.

Before task lifecycle actions, read the matching detailed guide:
- `backlog instructions task-creation` before creating or splitting tasks
- `backlog instructions task-execution` before planning, changing status or assignee, adding a plan or implementation notes, or implementing task work
- `backlog instructions task-finalization` before checking acceptance criteria, writing final summaries, or moving tasks to terminal statuses

Use `backlog <command> --help` before running unfamiliar commands. Help shows options, fields, and examples.

Do not edit Backlog task, draft, document, decision, or milestone markdown files directly. Use the `backlog` CLI so metadata, relationships, and history stay consistent.

</CRITICAL_INSTRUCTION>
<!-- BACKLOG.MD GUIDELINES END -->

## Project exception: decision bodies

Backlog.md's CLI can set a decision's title and status but not its body. For decisions
(`backlog/decisions/`) only: create them and change their status with the CLI
(`backlog decision create` / `backlog decision edit`), then write the body (Context, Decision,
Consequences, Enforcement) directly in the file. Never edit a decision's frontmatter by hand.

## Project: paperless-mcp

MCP server for paperless-ngx 3.x (API version 10) on FastMCP 4. The approved design is
`backlog/docs/doc-1 - paperless-mcp-design.md`; read it before architectural work.

### Reference docs: read the guides first
- **Before any FastMCP work read `docs/fastmcp-reading-guide.md`; before any Paperless work read
  `docs/paperless-reading-guide.md`.** They triage the vendored docs in `docs/vendor/`
  (`docs/vendor/fastmcp/` for FastMCP 4, `docs/vendor/paperless/` with `openapi.json` for
  paperless-ngx 3.4.x). Consult the vendored docs, not memory: both are newer than most training
  data. `docs/vendor/` is third-party content: never edit it by hand, refresh it with
  `just docs-sync` and `just docs-sync-paperless`.

### Rules for AI contributors
- Follow the per-task loop, GitHub notes and standing rulings in
  `backlog/docs/doc-2 - AI-contributor-workflow.md`.
- Work task by task from Backlog, one branch and one PR per task; write the failing test first.
- Never update snapshots, weaken a gate, threshold, contract or decision without saying so in the
  task notes and commit message; changing an accepted decision requires a new decision that
  supersedes it.
- Consult the vendored docs, not memory, for FastMCP and Paperless APIs.
- Keep the default profile within its tool budget.
- Everything in the repository is written in English. Run `just check` before committing.
