---
id: PLMCP-8
title: 'Walking skeleton: get_instance_overview through all layers'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-09T19:25:00Z'
updated_date: '2026-11-09T22:48:00Z'
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
- [x] #1 get_instance_overview (toolset core, read-only) returns statistics, system status, Paperless and API version, current user and capabilities (Paperless AI enabled) as structured output
- [x] #2 create_server wires settings, credential provider, adapter, services and providers; the server lifespan owns the single httpx2.AsyncClient
- [x] #3 Startup probe: a wrong API version or an invalid token fails fast with a clear message on stderr; unreachable Paperless still starts and the tool returns UpstreamUnavailable; the probe never blocks longer than its timeout
- [x] #4 stdio entrypoint as console script paperless-mcp and python -m paperless_mcp; all logging goes to stderr
- [x] #5 One error boundary maps domain errors to ToolError with message and hint; any other exception is masked
- [x] #6 Registry tests: every tool has a title, a description, annotations from the central table and exactly one toolset tag; destructive tools have confirm defaulting to false; no filesystem path parameter outside the stdio allowlist; the default profile has at most 20 tools
- [x] #7 A syrupy snapshot covers the MCP surface, plus schema-compatibility checks (object root, no $schema key, every parameter described)
- [ ] #8 README explains setup for Claude Code; verified manually with claude mcp add against a real instance
- [x] #9 Each tool call is logged with request id, tool name, duration and outcome; document content, tokens and full query text are never logged at INFO or above
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [x] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [x] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. services/instance.py: InstanceService(reader: InstanceReader) with overview() and probe(timeout): only UpstreamUnavailable or the timeout is tolerated (returns None); AuthenticationFailed, UnsupportedVersion and every other domain error propagate (fail fast). Unit tests against FakePaperless and small failing readers.
2. tools/_shared.py: TOOLSETS, the central ANNOTATIONS table (title + hints per tool name), the Services container and services(ctx) accessor (lifespan context), and the error boundary decorator (PaperlessMcpError -> ToolError(message + hint) from None; everything else left to mask_error_details=True).
3. tools/core.py: LocalProvider for the core toolset with get_instance_overview (read-only, structured InstanceOverview output, description explains null = not permitted / unknown).
4. observability.py (new layer between server and settings): configure_logging (JSON lines to stderr, httpx2/httpcore at WARNING), request-id ContextVar, CallLogging middleware (request id, tool, duration, outcome; never arguments or messages).
5. server.py: Ports + paperless_ports (the injection seam for FakePaperless), create_server(paperless, server, *, ports=paperless_ports): StaticTokenProvider, lifespan owning the one httpx2.AsyncClient, PaperlessClient, services, startup probe; FastMCP with providers, middleware, mask_error_details=True.
6. cli.py main() + __main__.py + console script paperless-mcp: logging setup, clear stderr message and exit 1 on invalid configuration or a fatal probe; no banner (no update check).
7. settings: PAPERLESS_MCP_STARTUP_TIMEOUT (probe bound).
8. Tests: service unit, tool tests (in-memory Client + FakePaperless via the ports seam), server/cli startup tests (stub HTTP server for 401/406, refused port for unreachable), logging tests (no query value/token/cause at INFO+), registry tests with synthetic violating providers (decision-4/6/7), syrupy surface snapshot + schema-compatibility checks, E2E stdio smoke of the console script against 3.4.2 incl. a bad token.
9. README: Claude Code setup (claude mcp add). import-linter/deptry updates, syrupy and mcp declared.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented the walking skeleton (TDD, RED then GREEN per level): services/instance.py InstanceService (overview, probe: only UpstreamUnavailable or the timeout tolerated); tools/_shared.py (TOOLSETS, central ANNOTATIONS table, Services + services(ctx), boundary); tools/core.py get_instance_overview; observability.py (JSON lines on stderr, httpx2/httpcore2 held at WARNING, request-id ContextVar, CallLogging middleware); server.py Ports/paperless_ports seam + create_server with lifespan owning the one httpx2.AsyncClient via paperless.client.connect (server may not import httpx2); cli.py main + __main__ + console script; PAPERLESS_MCP_STARTUP_TIMEOUT. PaperlessMcpError.describe() (message + Hint) shared by the boundary and the CLI.
Gate changes, deliberate: new import-linter layer 'observability' (between server and settings, added to the httpx2 contract); ruff per-file ignore TC001-3 for src/paperless_mcp/tools/*.py (FastMCP evaluates tool signatures at runtime); deptry DEP002 fastmcp exemption removed; mcp declared as a direct dependency (ToolAnnotations); syrupy added. New snapshot tests/tools/__snapshots__/test_surface.ambr: the first MCP surface (one tool). decision-6/7 Enforcement updated: their registry checks now exist (PLMCP-8) with self-tests.
Verification: just check green (188 passed, coverage 100 %, diff 100 %); just e2e 13 passed incl. stdio smoke of the console script and the wrong-token exit against 3.4.2. Verification against the user's own instance via claude mcp add is left to the user.

Fix round 1 (review + R17): confirm check accepts a described confirm (type boolean, default false) with a positive self-test; boundary now also catches non-domain exceptions (ERROR log names only the type, traceback at DEBUG, generic ToolError 'Internal error in <tool>; the server log has details.' from None) with a KeyError(content) log test; contract 6 renamed to include observability (pyproject comment, decision-1 Enforcement list updated); server binds ports(client) once; domain output models get one-line docstrings and Field descriptions (snapshot regenerated deliberately, -68 lines: no Attributes blocks, ComponentStatus docstring one line); seeded accounts moved to tests/fakes/accounts.py (e2e.helpers imports them). just check 190 passed, 100 %; just e2e 13 passed.

Task review (opus): 2 Important (confirm check vs described param; non-domain exception logging) + R17 minors fixed in 2fc113e; scoped re-review clean.
AC8: README documents claude mcp add; automated stdio smoke against E2E 3.4.2 passes (R9). Manual check against the user's own instance is still open → task stays In Progress until the user confirms.
DoD1 not checked: implementer reports server/observability/settings tests were partly written after the code.
Carry to next tool task: boundary must re-raise ToolError before the catch-all (+ test).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Walking skeleton: get_instance_overview (core toolset, read-only, structured output) runs tools → services → InstanceReader port → adapter, assembled in create_server with the lifespan owning the single httpx2 client. Startup probe fails fast on wrong API version or invalid token and tolerates an unreachable Paperless. stdio console script paperless-mcp / python -m paperless_mcp; logging to stderr with httpx2/httpcore2 at WARNING; one error boundary (domain errors → ToolError with hint; anything else logged by type only and masked). Registry tests with offender and positive self-tests, syrupy snapshot of the MCP surface with schema-compatibility checks, tests at unit/tool/contract/e2e level incl. a stdio smoke of the real console script.
Open: manual verification against the user's own Paperless; ToolError re-raise in boundary; FastMCP stderr lines not JSON; probe does not check Paperless major version.
<!-- SECTION:FINAL_SUMMARY:END -->
