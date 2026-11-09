---
id: PLMCP-1
title: Scaffold project and local quality gates
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-09T19:24:00Z'
updated_date: '2026-11-09T19:53:00Z'
labels: []
milestone: m-0
dependencies: []
references:
  - /Users/fabian/Documents/Git/dynasoft/Dynasoft-AI/tosca.mcp
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: chore
ordinal: 1000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Every later task needs one local gate that mirrors CI, and the layer boundaries of the design must be machine-checked from the first commit because AI agents write the code. The base setup comes from tosca.mcp (uv, justfile, gates), trimmed of everything tosca-specific (design §8: knowledge/dense/graph components, ONNX/numpy, Ladybug, telemetry, Entra, OpenSpec, Gitea workflows).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 uv project with uv_build and requires-python >=3.14; runtime dependencies fastmcp 4.1.x, pydantic, pydantic-settings and httpx2; httpx is not a declared dependency
- [ ] #2 Package src/paperless_mcp has the packages domain, services, paperless and tools plus server, settings and entrypoint modules from design §5.1
- [ ] #3 import-linter contracts encode every import rule of design §5.1 (layers, forbidden imports, toolset independence, httpx2 only in paperless, no httpx) and pass
- [ ] #4 ruff (select ALL with a documented ignore list, Google docstring convention), basedpyright strict on src and tests, and deptry pass
- [ ] #5 Suppressions (noqa, type: ignore, pragma: no cover) without a specific code and a reason comment fail lint; unused suppressions fail
- [ ] #6 unittest.mock is banned in src and tests via ruff banned-api; the only test seams are constructor injection and the HTTP transport
- [ ] #7 just check runs lint, format check, type check, import contracts, deptry and the tests with branch coverage >= 95 % overall and 100 % on changed lines (diff-cover)
- [ ] #8 pre-commit (including gitleaks) runs the same checks; MIT LICENSE, .gitignore, .editorconfig and a minimal README exist
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. pyproject.toml: uv_build backend, requires-python >=3.14, runtime deps fastmcp>=4.1.6,<4.2 + pydantic + pydantic-settings + httpx2 (no httpx); dev group with ruff, basedpyright, import-linter, deptry, pytest, pytest-cov, coverage, diff-cover, pre-commit; .python-version 3.14.
2. Package skeleton per design 5.1: src/paperless_mcp/{domain,services,paperless,tools}/__init__.py, server.py, cli.py, __main__.py, app.py (docstring-only, no server logic; filled by PLMCP-6..8).
3. TDD: tests/test_settings.py and tests/test_version.py first (RED), then settings.py (PaperlessSettings: PAPERLESS_URL, PAPERLESS_TOKEN as SecretStr, PAPERLESS_PUBLIC_URL optional; ServerSettings with PAPERLESS_MCP_ prefix: log_level) and __version__ from distribution metadata (GREEN).
4. Gates in pyproject: ruff select ALL with a documented ignore list, Google docstrings, banned-api unittest.mock/mock (+ httpx), absolute imports; basedpyright (try recommended, else strict + rules) on src and tests with rule-specific, used ignore comments only; pytest strict + warnings as errors; coverage branch fail_under 95; deptry; import-linter contracts for every 5.1 rule whose modules exist (toolset independence deferred with a note, R2). Exclude docs/vendor, .superpowers, backlog (R5).
5. justfile: lint (ruff + suppression/patching hygiene grep), format, format-check, typecheck, imports, deps, test, cov-diff base=main (diff-cover 100 %), check, audit (pip-audit + .pip-audit-ignore), precommit.
6. .pre-commit-config.yaml running the same recipes plus file hygiene hooks and gitleaks (.gitleaks.toml allowlisting docs/vendor); pre-commit as dev dependency.
7. LICENSE (MIT), .gitignore, .editorconfig, minimal README.
8. Verify: just check green, pre-commit green; temporary probes prove each gate bites (forbidden import, unexplained noqa, unittest.mock in a test, coverage drop); revert probes, commit on plmcp-1-scaffold.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented scaffold and gates. Choices: basedpyright `recommended` (all rules, fails on warnings; no FastMCP code yet, so the strict fallback is untested); import-linter uses `forbidden` contracts for external packages because `protected` fails while httpx2/fastmcp are not imported yet; layers contract is exhaustive over paperless_mcp, settings sits between server and tools|paperless; suppression format `# noqa: CODE -- reason` / `# pyright: ignore[rule] -- reason` / `# pragma: no cover -- reason` enforced by `just hygiene` (part of `just lint`), `# type: ignore` disabled (blanket in pyright); monkeypatch.setattr & co. banned by the same check. Toolset independence contract deferred (R2). deptry DEP002 ignores fastmcp and httpx2 until their first import (PLMCP-6/8). Gate-bite probes recorded in the task-1 report.
<!-- SECTION:NOTES:END -->
