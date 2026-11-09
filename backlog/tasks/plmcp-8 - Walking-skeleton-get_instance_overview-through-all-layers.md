---
id: PLMCP-8
title: 'Walking skeleton: get_instance_overview through all layers'
status: To Do
assignee: []
created_date: '2026-11-09T19:25:00Z'
updated_date: '2026-11-09T19:26:00Z'
labels: []
milestone: m-0
dependencies:
  - PLMCP-4
  - PLMCP-7
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 8000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Prove the architecture end to end before features pile on: one tool through tools, services, ports and adapter, assembled in the composition root and usable from Claude Code over stdio (design §10, M0 exit criterion). The registry tests and the contract snapshot are introduced here with the first tool.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 get_instance_overview (toolset core, read-only) returns statistics, system status, Paperless and API version, current user and capabilities (Paperless AI enabled) as structured output
- [ ] #2 create_server wires settings, credential provider, adapter, services and providers; the server lifespan owns the single httpx2.AsyncClient
- [ ] #3 Startup probe: a wrong API version or an invalid token fails fast with a clear message on stderr; unreachable Paperless still starts and the tool returns UpstreamUnavailable; the probe never blocks longer than its timeout
- [ ] #4 stdio entrypoint as console script paperless-mcp and python -m paperless_mcp; all logging goes to stderr
- [ ] #5 One error boundary maps domain errors to ToolError with message and hint; any other exception is masked
- [ ] #6 Registry tests: every tool has a title, a description, annotations from the central table and exactly one toolset tag; destructive tools have confirm defaulting to false; no filesystem path parameter outside the stdio allowlist; the default profile has at most 20 tools
- [ ] #7 A syrupy snapshot covers the MCP surface, plus schema-compatibility checks (object root, no $schema key, every parameter described)
- [ ] #8 README explains setup for Claude Code; verified manually with claude mcp add against a real instance
- [ ] #9 Each tool call is logged with request id, tool name, duration and outcome; document content, tokens and full query text are never logged at INFO or above
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
