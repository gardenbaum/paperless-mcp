---
id: decision-8
title: Test strategy
date: '2026-11-09T20:44:00Z'
status: accepted
---
## Context

Mocks and patching hide integration bugs and lock tests to implementation. The code is written by agents, which makes shortcuts (a suppression comment, a patched internal) cheap and easy. Design 9 sets the strategy; this decision records the choices and the known gap.

## Decision

- Fakes over mocks: an in-memory `FakePaperless` implements the ports with realistic behaviour; a shared contract suite runs against the fake and the real adapter against live Paperless (E2E), so the fake cannot drift. Seams are constructor injection and the HTTP transport only. `unittest.mock`, `mock`, `pytest_mock` and patching (`monkeypatch.setattr`, `setattr`, `delattr`, `setitem`, `delitem`) are banned.
- Adapter tests validate every request and recorded response against the vendored OpenAPI schema (openapi-core).
- Levels: unit/property (hypothesis), adapter, tool (FastMCP in-memory client), contract (syrupy snapshot), E2E (Paperless 3.4.2 in docker compose), mutation (mutmut, at least 80 % on `services/` and `paperless/`, report-only until PLMCP-21).
- Gates: branch coverage at least 95 %, 100 % on changed lines (diff-cover); ruff `select = ["ALL"]`; basedpyright in `recommended` mode on `src` and `tests`, no baseline file; import-linter; deptry; pip-audit; gitleaks.
- R10: suppressions are only `# noqa: CODE -- reason` and `# pyright: ignore[rule] -- reason`; `# type: ignore`, inline `# pragma` and file or range directives are banned. Unused suppressions fail.
- Known gap: the `ai` toolset needs an LLM backend, so CI covers it only with schema-validated adapter tests and fake-based tool tests; a live AI run is an opt-in local recipe (`just e2e-ai`). The "honest fake" guarantee does not extend to it.

## Consequences

- `recommended` was chosen over `strict`: it enables every rule (some as warnings) and fails on warnings, so it is stricter than `strict`. ty and pyrefly are beta, mypy is weaker with Pydantic and FastMCP generics. Fallback if FastMCP or Pydantic typing proves too noisy: `strict` plus selected rules, recorded in a new decision.
- Tests need more setup than patching; accepted.
- Weakening a gate or threshold requires a new decision and a note in the task and commit message.

## Enforcement

- `just hygiene` (inside `just lint`, hence `just check` and CI): fails on any suppression outside the allowed forms and on patching calls.
- ruff banned-api in `pyproject.toml`: `unittest.mock`, `mock`, `pytest_mock`, `httpx`.
- `typeCheckingMode = "recommended"` in `tool.basedpyright`, `just typecheck` (fails if `.basedpyright/baseline.json` exists).
- Coverage `fail_under = 95` and `just cov-diff` (100 % on changed lines) in CI.
- Shared contract suite and fake: PLMCP-7; schema-guard transport: PLMCP-6; E2E: PLMCP-5; mutation gate: PLMCP-14 and PLMCP-21.
- The `ai` gap is recorded here and in design 9.2; no automated guard (manual review: needs an external LLM backend).
