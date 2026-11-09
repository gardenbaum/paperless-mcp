---
id: doc-1
title: paperless-mcp design
type: specification
created_date: '2026-11-09T19:21:00Z'
updated_date: '2026-11-09T19:26:00Z'
---
# paperless-mcp — Design

Status: approved 2026-11-09 · Plan: Backlog milestones m-0..m-6 (PLMCP-1..37) · Owner: Fabian

## 1. Intent

A complete, production-grade MCP server for **paperless-ngx 3.x**, written in Python on
**FastMCP 4**. It lets an LLM client (Claude Code / Claude Desktop today, claude.ai later) do
everything the Paperless API allows: find, read, file, edit, upload, organise and administer
documents.

Why a new server instead of one of the three existing ones (pvliesdonk, baruchiro,
OrellBuehler): none of them combines Paperless 3.x coverage, safe write semantics, name-based
input and a test suite that runs against a real Paperless. The project's defining quality bar is
**architecture and engineering rigour** at the level of a senior Python team at Google. Because the
code is written by AI agents, the architecture must be **enforced by automated checks**, not by
convention or memory.

## 2. Goals, non-goals, anti-goals

**Goals**
- Cover the full paperless-ngx 3.x API surface that is meaningful to an LLM, organised into
  toolsets; a small core is on by default, everything else is opt-in via configuration.
- Accept human names wherever Paperless expects IDs (tags, correspondents, document types,
  storage paths, custom fields, saved views, users, groups).
- Safe writes: no silent data loss, destructive operations need explicit confirmation, a
  read-only mode exists.
- Token economy: compact list rows, never OCR text in lists, content delivered in slices.
- Local single-user operation now (stdio + one Paperless API token); the architecture must allow
  remote use from claude.ai (HTTP + OAuth) next and multiple users third — without rewrites.
- Every architecture decision has an automated guard that fails CI when it is violated.

**Non-goals (for now)**
- paperless-ngx 2.x support.
- Own embeddings / vector search (Paperless 3.x ships its own AI: RAG chat and AI suggestions).
- Server-side "agentic" workflows such as auto-classify or inbox processing as tools; those are
  prompts the LLM runs with the normal tools.
- Publishing to PyPI / a marketplace; LLM-based evaluation of tool selection.

**Anti-goals (would make the project a failure even if it works)**
- A 1:1 mirror of the REST API (auto-generated tools, hundreds of endpoints in the default list).
- Any path where a tool call silently drops data (e.g. replacing the custom-field list on PATCH).
- Access to the MCP host's filesystem or network on behalf of a remote caller (arbitrary paths,
  URL fetching → SSRF).
- Tests that only re-state the implementation (mock-heavy, tautological); an architecture that
  erodes because nothing checks it.

## 3. Constraints

- paperless-ngx **3.x only**, API version **10** (current default; available in every 3.x
  release, 2.x stops at 9). Reference version for development and E2E: **3.4.2**.
- FastMCP **4.1.x** (latest stable 4.1.6; Provider model, MCP SDK v2). Its HTTP stack is
  **httpx2**; this project uses httpx2 too (no `httpx`, no second HTTP library).
- Python **3.14** baseline (`requires-python >=3.14`); CI also runs 3.15.
- **Public** repository on GitHub (`gardenbaum/paperless-mcp`, personal account), CI on GitHub
  Actions, dependency updates via Renovate. `main` is protected: changes land through one branch
  and one pull request per Backlog task, merge only with all required checks green.
- Project management with **Backlog.md** only: this design is a Backlog document, decisions are
  Backlog decisions, the plan is Backlog tasks/milestones. No OpenSpec, no separate ADR folder.
  Decision frontmatter and status are changed only via the CLI; the decision body (context,
  decision, consequences, enforcement) is written directly in the file — the one documented
  exception to the "no direct edits" rule in `CLAUDE.md`/`AGENTS.md`.
- Everything in the repository is written in English (code, tool descriptions, decisions, docs).
- The base setup mirrors `tosca.mcp` (uv, justfile, gates, vendored docs), trimmed of everything
  tosca-specific.

## 4. Approaches considered

| | Approach | Trade-off | Verdict |
|---|---|---|---|
| **A** | New build, layered (ports & adapters), hand-written models; the vendored OpenAPI schema is used to **verify** requests/responses, not to generate code | Most hand-written code; every line intentional, typed, readable; best testability | **Chosen** |
| B | Like A, but the client is generated from the OpenAPI schema | Full low-level coverage instantly; but >10k lines nobody reviews, inherits schema bugs (Paperless fixed wrong list types only in 3.4.0), generator becomes a critical dependency, noisy under strict typing; upper layers stay manual anyway | Rejected |
| C | Fork pvliesdonk/paperless-mcp (Python, FastMCP 4.1.6) and refactor | ~50 working tools on day one; but bound to the author's `fastmcp-pvl-core` framework and copier template, many tests test the template; reaching the target architecture touches nearly every file | Rejected (architecture must not be compromised); used as **reference** (MIT) |
| — | `FastMCP.from_openapi` auto-mirroring | FastMCP docs state curated servers perform significantly better; no place for name resolution or merge semantics | Dropped |

The three existing servers serve as references; their good ideas are adopted explicitly
(attributed in the decision or code comment where code-level patterns are borrowed):
- pvliesdonk: field projection in lists, content slicing with next offset, idempotent-only
  retries, token redaction, errors that name the next call, central annotation table, `web_url`.
- baruchiro: names in output, `confirm: true` for destructive calls, upload allowlist, live E2E
  against real Paperless, a test over every advertised tool schema for client compatibility.
- OrellBuehler: toolsets + read-only mode + "no tool left unclassified" test, Paperless 3.x
  features (versions, AI suggestions, trash, PDF operations), prompts that ask for confirmation,
  a test that every tool named in a prompt exists.

## 5. Architecture

### 5.1 Layers

```
cli.py / __main__.py / app.py      entrypoints (stdio, HTTP/ASGI)
        │
server.py                          composition root: settings → adapter → services → providers
        │                          the ONLY module that imports both tools/ and paperless/
   ┌────┴─────────────┐
tools/                paperless/   tools: FastMCP providers per toolset (thin)
   │                     │         paperless: httpx2 client + wire models (adapter)
services/                │         services: use cases, pure logic, depend on ports only
   │                     │
domain/  ◄───────────────┘         domain: models, value objects, errors, ports (Protocols)
```

Import rules (enforced, see §9.4):
- `domain` imports nothing from the package and no third-party I/O or MCP library.
- `services` imports `domain` only; never `fastmcp`, `mcp`, `httpx2`, `paperless`.
- `paperless` imports `domain` (to implement ports and raise domain errors); never `fastmcp`,
  `mcp`, `services`, `tools`.
- `tools` imports `services` and `domain`; never `httpx2` or `paperless`.
- Only `paperless` imports `httpx2`. Only `tools`, `server`, entrypoints import `fastmcp`/`mcp`.
- Toolset modules inside `tools/` are independent of each other (shared helpers live in
  `tools/_shared`).
- Nothing imports the entrypoints.

### 5.2 Domain (`domain/`)

- **Ports**: narrow `typing.Protocol`s per resource and capability, split into read and write
  (e.g. `DocumentReader`, `DocumentWriter`, `TaxonomyReader`, `TaxonomyWriter`, `TaskReader`,
  `AiGateway`, …). Services depend on the smallest protocol they need.
- **Domain models**: immutable Pydantic models (`frozen=True`) that services return and tools
  expose as structured output (compact `DocumentSummary`, full `DocumentDetail`, `ContentSlice`,
  `TaxonomyItem`, `TaskStatus`, …). Wire details (Paperless field names, nested URL strings) do
  not leak into domain models.
- **Errors**: a `PaperlessMcpError` hierarchy with client-safe messages and an optional `hint`
  naming the next useful tool (`NotFound`, `Ambiguous`, `Validation`, `PermissionDenied`,
  `Conflict`, `UpstreamUnavailable`, `UnsupportedVersion`, `ConfirmationRequired`,
  `FeatureUnavailable`).

### 5.3 Paperless adapter (`paperless/`)

- One `httpx2.AsyncClient` per process, created in the server lifespan; base URL, timeouts
  (separate for uploads), connection limits; the transport is injectable for tests.
- Every request sends `Accept: application/json; version=10` and the auth header supplied by the
  current credential.
- Credentials come from a `CredentialProvider` seam that returns an opaque `PaperlessCredential`
  (the auth header material plus a stable, non-secret fingerprint). Today: one static API token
  from settings (`Authorization: Token …`). Later: resolved per MCP principal (§5.7). The adapter
  never reads settings or globals directly, and nothing outside the provider knows the credential
  scheme.
- Retries only for idempotent methods (GET/HEAD/PUT/DELETE where safe) on 429/502/503/504,
  honouring `Retry-After`, bounded attempts with jitter.
- Pagination helper with a hard item cap; anything truncated is reported (`truncated: true`), never
  silently cut. `next`/`previous` URLs are reduced to page numbers (no host leakage).
- Wire models (Pydantic) mirror only what the project uses; unknown fields ignored; mapping to
  domain models lives in the adapter.
- HTTP errors map to domain errors; Paperless field validation messages are passed through
  sanitised; tokens and auth headers are redacted from every log line and exception.
- Startup probe (short timeout): wrong API version or invalid token → fail fast with a clear
  message on stderr; Paperless unreachable → start anyway, tools return `UpstreamUnavailable`.
  The probe also detects optional capabilities (Paperless AI enabled, current user, superuser).

### 5.4 Services (`services/`)

Pure use-case logic, fully testable against in-memory fakes:
- **Name resolution** (`TaxonomyResolver`): resolves name-or-id inputs for every taxonomy kind.
  Rules: numeric input = id; otherwise exact match, then case-/accent-insensitive match; more than
  one match → `Ambiguous` listing candidates; no match → `NotFound` with closest suggestions.
  Never auto-picks a fuzzy match. Backed by a per-credential cache with full pagination, TTL,
  and invalidation on the server's own writes. Optional auto-create only where a tool
  explicitly offers it (e.g. `create_missing_tags=true`).
- **Document query building**: structured filters (names/ids, date ranges, tags all/any/none,
  owner, inbox, custom-field queries incl. relative dates, full text, more-like) → Paperless
  query parameters. Full-text uses Paperless's Tantivy query syntax unchanged.
- **Projection**: list rows never contain OCR content; ids are enriched with names; custom-field
  values are rendered readably (select option labels, monetary, document links, dates).
- **Content slicing**: `offset`/`max_chars` with `total_chars` and `next_offset`.
- **Write semantics**: document updates are computed as a diff against current state;
  tags and custom fields support add/remove/set with **merge semantics** (Paperless's PATCH
  replaces the whole custom-field list, the service always sends the merged list).
  Bulk operations offer `dry_run` (returns affected count and sample) before execution.
- **Upload**: validates size, MIME type and filename; maps metadata via the resolver; returns the
  consumption task; optional bounded wait for completion.

### 5.5 Tools (`tools/`)

- One FastMCP `LocalProvider` per toolset module, mounted by the composition root.
- Every tool: `snake_case` verb-first name; a title; an LLM-oriented description (what it does,
  when to use it instead of a sibling tool); typed parameters with descriptions (shared
  `Annotated` parameter types); `ToolAnnotations` (`read_only_hint`, `destructive_hint`,
  `idempotent_hint`, `open_world_hint`) from one central table; exactly one toolset tag; a typed
  return model (structured output).
- Destructive tools require `confirm: bool = False`; without it they return what *would* happen
  and how to confirm. Elicitation is not used for confirmation (raises on the sessionless MCP
  2026-07-28 era).
- Tools translate domain errors via one boundary decorator into `ToolError` (client-safe message
  + hint); everything else is masked (`mask_error_details=True`).
- Every document-shaped output carries `web_url` (from `PAPERLESS_PUBLIC_URL` or the base URL).
- Binary data (thumbnails, originals, archives) is delivered as MCP image/embedded-resource
  content with a size cap; above the cap, metadata + `web_url` only.

### 5.6 Resources and prompts

- Resources: `paperless://taxonomy/{kind}` (compact id/name tables), `paperless://documents/{id}`,
  `…/content`, `…/thumbnail`, `…/original`, `…/archive`. Resources are a second access path, not
  a replacement for tools (many clients ignore resources).
- Prompts (guided multi-step tasks, always propose before applying): `triage_inbox`,
  `find_document`, `annual_review` (e.g. tax preparation), `cleanup_taxonomy`. A test asserts that
  every tool a prompt names exists and is enabled in the toolsets the prompt declares.

### 5.7 Transports, authentication, users

- **Now**: stdio (Claude Code/Desktop) with a static Paperless token; logs go to stderr only.
- **HTTP** (Streamable HTTP via `app.py`): secure by default — refuses to start without an auth
  configuration unless explicitly bound to loopback with `allow_unauthenticated_loopback=true`.
  First auth mode: static bearer token(s) for the MCP endpoint. Host allowlist
  (TrustedHostMiddleware) and request-id middleware as in tosca.mcp.
- **claude.ai (next)**: FastMCP `OIDCProxy`/`OAuthProxy` in front of the user's identity provider
  (supports DCR, CIMD and the claude.ai callback). Plugged in at the composition root; no change
  below `server.py`.
- **Multi-user (third)**: the `CredentialProvider` maps the authenticated MCP principal to that
  user's Paperless credential; caches are keyed by the credential fingerprint; Paperless enforces
  permissions.
- **Deferred on purpose**: which identity provider M5 uses and where per-user Paperless
  credentials come from in M6 (admin-kept mapping, user-supplied tokens, or a trusted upstream
  header). Both are decided as Backlog decisions at the start of their milestone; the seam above
  is shaped so that neither answer changes code below `server.py` and the provider.
- Filesystem/network rules: server-side file paths are accepted **only** in stdio mode and only
  inside configured allowlisted directories; HTTP mode accepts base64 uploads up to a size limit.
  No tool fetches arbitrary URLs.

### 5.8 Configuration

`pydantic-settings`, `PAPERLESS_MCP_*` prefix for server settings; `PAPERLESS_URL`,
`PAPERLESS_TOKEN` (SecretStr), `PAPERLESS_PUBLIC_URL` for the connection (compatible with other
Paperless tools). Toolset selection: `PAPERLESS_MCP_TOOLSETS` (list or `all`, default `core`),
`PAPERLESS_MCP_READ_ONLY` (hides every tool without `read_only_hint`),
`PAPERLESS_MCP_DISABLED_TOOLS`. Startup fails if the filters leave zero tools. Toolsets whose
Paperless capability is missing (e.g. AI disabled) are hidden automatically.

## 6. Tool catalogue

Names are indicative; the builder finalises parameters within §5.5. "Phase" refers to §10.

| Toolset (default) | Tools | Phase |
|---|---|---|
| **core** (on) — read | `search_documents` (full text, more-like, structured filters by name/id, dates, custom-field query, paging, ordering) · `get_document` (detail + names; `include`: notes, history, metadata, versions, suggestions) · `get_document_content` (sliced) · `get_document_thumbnail` · `download_document` (original/archive) · `list_taxonomy(kind, name_contains)` · `get_instance_overview` (statistics, system status, versions, current user, capabilities) · `get_task(task_id, wait_seconds)` | M1 |
| **core** (on) — write | `update_document` (merge semantics) · `add_note` · `delete_note` · `delete_document` (→ trash, confirm) · `upload_document` (base64 / stdio allowlisted path; metadata by name; optional wait) · `create_taxonomy_item` · `update_taxonomy_item` · `delete_taxonomy_item` (confirm) | M2 |
| **ai** (on if Paperless AI enabled) | `ask_paperless_ai` (RAG chat over all or given documents) · `get_ai_suggestions` | M2 |
| bulk | `bulk_edit_documents` (typed operations: tags add/remove/modify, correspondent, type, storage path, custom fields, delete, reprocess; `dry_run`, confirm) · `bulk_edit_taxonomy` (delete, permissions) | M3 |
| pdf | `rotate_documents` · `merge_documents` · `split_document` · `delete_pages` · `edit_pdf` · `remove_document_password` · `reprocess_documents` | M3 |
| versions | `list_document_versions` · `upload_document_version` · `set_document_version_label` · `delete_document_version` (confirm) · `merge_documents_as_versions` | M3 |
| views | saved views list/get/create/update/delete · `run_saved_view` (translates the view's filter rules into a document query) | M3 |
| sharing | share links list/create/delete · share-link bundles list/create/rebuild/delete · `email_documents` | M3 |
| trash | `list_trash` · `restore_from_trash` · `empty_trash` (explicit ids or `all=true`, confirm) | M3 |
| permissions | `set_permissions` (documents and taxonomy; owner, view/change users/groups; merge or replace) · `list_users` · `list_groups` | M3 |
| mail | mail accounts list/get/create/update/delete/test/process (passwords write-only, never returned) · mail rules CRUD · `list_processed_mail` | M4 |
| workflows | workflows list/get/create/update/delete (triggers and actions nested) | M4 |
| admin | users CRUD · groups CRUD · application config get/update · logs list/get · tasks list/acknowledge/run (e.g. classifier training, sanity check) · `bulk_download` | M4 |
| raw (off) | `paperless_api_get(path, params)` — read-only GET under `/api/`, size-capped, for anything not covered | M4 |

Target size of the default profile (`core` + `ai`): ≤ 20 tools; a test enforces the budget.

## 7. Cross-cutting rules

- **Token economy**: default page size 25, max 100; content slice default 20 000 characters;
  aggregated fetches capped (e.g. 2 000 items) with `truncated`; descriptions concise.
- **Safety**: destructive ⇒ `confirm` + `destructive_hint`; deletes go to trash where Paperless
  supports it; `empty_trash` never defaults to "everything"; read-only mode; size limits on all
  inbound payloads; secrets redacted everywhere; mail passwords never echoed.
- **Errors**: client-safe message + hint naming the next tool; upstream 4xx/5xx mapped by status;
  no stack traces to the client.
- **Observability**: structured logging to stderr, request id per call, tool name + duration +
  outcome; never document content, tokens or full queries at INFO.

## 8. Repository and tooling

Kept from `tosca.mcp` (trimmed and adapted): uv + `uv_build`, justfile (`check` as the one local
gate that mirrors CI), composition root pattern, error boundary + `mask_error_details`, request-id
middleware, settings pattern, Dockerfile pattern (multi-stage, **non-root**), pre-commit, gitleaks,
pip-audit with ignore file, Renovate, contract snapshot test, `docs-sync` recipe and reading guide.

Dropped: knowledge/dense/graph components, ONNX/numpy deps, Ladybug docs, telemetry evaluation,
Entra concept, OpenSpec, `docs/adr/`, Gitea workflows (→ GitHub Actions).

Tooling choices: ruff (`select = ["ALL"]` with a documented ignore list; Google docstring
convention), basedpyright `strict` on `src` and `tests` (ty is still beta), import-linter, deptry,
pytest + pytest-asyncio (or anyio), hypothesis, syrupy, openapi-core, mutmut, diff-cover. HTTP
mocking uses httpx2's built-in `MockTransport` (no respx).

Vendored reference docs (committed, refreshed by just recipes, each with an `UPSTREAM` marker):
- `docs/vendor/fastmcp/` — `docs/` tree of the latest stable FastMCP tag + reading guide
  (`docs/fastmcp-reading-guide.md`, rewritten for this project).
- `docs/vendor/paperless/` — `openapi.json` generated from the pinned Paperless image
  (drf-spectacular), plus `docs/api.md` and the search/usage docs from the same tag + reading
  guide.
- Agent instructions (`CLAUDE.md`/`AGENTS.md`): read the reading guides before FastMCP/Paperless
  work; the rules in §9.6.

## 9. Test concept and architecture enforcement

### 9.1 Principles
- **Fakes over mocks.** Services and tools are tested against an in-memory `FakePaperless` that
  implements the ports with realistic behaviour (pagination, permissions errors, PATCH semantics).
  Patching internals (`unittest.mock.patch`, `monkeypatch.setattr` on project modules) is banned
  in tests by lint rule; the only seams are constructor injection and the HTTP transport.
- **The fake is honest**: a shared contract test suite runs against both `FakePaperless` and the
  real adapter + live Paperless (E2E), so the fake cannot drift from reality.
- **Behaviour, not implementation**: tests assert observable outcomes (returned models, requests
  sent, state in the fake), not call sequences.
- **TDD** for every change (red → green → refactor).

### 9.2 Test levels

| Level | What | Tooling | Runs |
|---|---|---|---|
| Unit | domain + services (resolver, query builder, merge, slicing, projection) incl. property tests | pytest, hypothesis, FakePaperless | every commit |
| Adapter | `paperless/` against `MockTransport`; **every request and every recorded response is validated against the vendored OpenAPI schema** by a schema-guard transport | openapi-core | every commit |
| Tool | tools via FastMCP in-memory `Client` + FakePaperless; structured output, errors, confirm flow, visibility per toolset/read-only | fastmcp Client | every commit |
| Contract | snapshot of the full MCP surface (names, titles, descriptions, input/output schemas, annotations, tags) per profile; schema-compatibility checks (object root, no `$schema`, every parameter described, no unsupported keywords) | syrupy | every commit |
| E2E | real paperless-ngx 3.4.2 (+ Redis) via docker compose, seeded through the API; runs the shared contract suite and key tool flows | docker compose, pytest marker `e2e` | every PR; weekly against `latest` 3.x |
| Mutation | `services/` and `paperless/`; surviving mutants become Backlog tasks | mutmut | weekly + on demand |

Gates: branch coverage ≥ 95 % overall and 100 % on changed lines (diff-cover); mutation score
≥ 80 % on the two packages (start as report-only, enforce from M2).

Paperless AI needs an LLM backend, so the `ai` toolset is covered in CI by schema-validated
adapter tests and fake-based tool tests only. A live AI run is an opt-in local recipe
(`just e2e-ai`) against a backend the developer provides via environment. This is a known gap in
the "honest fake" guarantee and is recorded as such in the test-strategy decision.

### 9.3 Static gates
ruff (lint + format), basedpyright strict, import-linter, deptry, pip-audit, gitleaks — all behind
`just check` (offline gates) and identical CI jobs. Suppressions (`noqa`, `type: ignore`,
`pragma: no cover`) require a specific code and a reason comment; unused suppressions fail.

### 9.4 Architecture fitness functions
- **import-linter contracts** for every rule in §5.1 (layers, forbidden imports, toolset
  independence, `httpx2` only in `paperless`, no `httpx`).
- **Registry tests** over the assembled server (all toolsets enabled):
  every tool has a title, description, annotations from the central table and exactly one toolset
  tag; every destructive tool has a `confirm` parameter defaulting to false; no tool exposes a
  filesystem path parameter outside the stdio-only allowlist; default-profile tool budget;
  read-only mode leaves only `read_only_hint` tools; every prompt's referenced tools exist.
- **Write guard**: `FakePaperless` rejects write calls unless a test opts in, so a read-only tool
  that writes fails its own tests.
- **Decision ↔ enforcement link**: every accepted Backlog decision has an `## Enforcement`
  section naming its guard (contract, test, lint rule, or "manual review" with reason); a test
  parses `backlog/decisions/` and fails on accepted decisions without it. Guarding tests carry a
  `@pytest.mark.decision("decision-N")` marker; a test checks every referenced id exists.

### 9.5 CI (GitHub Actions)
`check` (lint, format, types, imports, deps, unit/adapter/tool/contract tests, diff-cover) ·
`e2e` (compose stack) · `audit` (pip-audit, gitleaks) · `docker` build on main/tags · weekly:
E2E against latest 3.x, mutation run, drift check of vendored Paperless schema/docs against the
latest 3.x release (opens an issue on change). Python 3.14 and 3.15 matrix for `check`.
Required checks for merging into `main`: `check` (both Python versions), `e2e`, `audit`
(public repository: Actions minutes are not a constraint, branch protection is available).

### 9.6 Rules for AI contributors (in CLAUDE.md/AGENTS.md)
- Work task by task from Backlog, one branch and one PR per task; write the failing test first.
- Never update snapshots, weaken a gate, threshold, contract or decision without saying so in the
  task notes and commit message; changing an accepted decision requires a new decision that
  supersedes it.
- Consult the vendored docs, not memory, for FastMCP and Paperless APIs.
- Keep the default profile within its tool budget.

## 10. Delivery phases (Backlog milestones)

| Milestone | Content | Exit criterion |
|---|---|---|
| **M0 Foundation** | scaffold from tosca.mcp, gates, GitHub Actions, vendored docs + reading guides, initial decisions (§11) accepted, E2E stack, **walking skeleton**: `get_instance_overview` through all layers, tested on every level | `just check` and CI green, server usable from Claude Code |
| **M1 Core read** | resolver + cache, search/get/content/thumbnail/download/taxonomy/task, resources | daily read use from Claude Code |
| **M2 Core write + AI** | update (merge), notes, delete, upload, taxonomy CRUD, ai toolset, mutation gate on | core profile complete |
| **M3 Extended** | bulk, pdf, versions, views, sharing, trash, permissions | |
| **M4 Admin** | mail, workflows, admin, raw, prompts | full catalogue |
| **M5 Remote** | HTTP hardening, Docker (non-root) on GHCR, OIDC/OAuth proxy for claude.ai, deployment guide | usable as claude.ai connector |
| **M6 Multi-user** | per-principal credentials, per-credential caches | |

## 11. Initial decisions (Backlog decisions, with their guard)

1. Layered architecture (ports & adapters) — import-linter contracts.
2. paperless-ngx 3.x / API v10 only — adapter test that every request carries `version=10` and
   the credential's auth header, startup version check, E2E pin.
3. httpx2 as the only HTTP library, injectable transport — import-linter + banned import.
4. Tool design rules (toolsets, annotations, naming, structured output, budget) — registry tests +
   snapshot.
5. Name-resolution semantics (no fuzzy auto-pick, ambiguity is an error) — property tests.
6. Write safety (merge semantics, confirm, trash, read-only mode) — registry + service tests.
7. No host filesystem/network access for remote callers — registry test + adapter design.
8. Test strategy (fakes over mocks, schema-validated adapter tests, E2E, mutation) — lint ban on
   patching, CI gates.
9. Auth seam (stdio/no auth, HTTP secure by default, OIDC proxy later, credential provider) —
   startup tests.
10. Process: Backlog.md only (incl. the decision-body exception), English in the repository, PR
    per task with required checks — branch protection settings + manual review.

## 12. Calls made without explicit user input (please check)

English in the repo · Python 3.14 baseline (+3.15 in CI) · MIT licence · basedpyright over ty ·
httpx2 + MockTransport instead of respx · docker compose instead of testcontainers ·
openapi-core for schema validation · `core` (+`ai`) as the only default toolsets, bulk opt-in ·
API v10 only, no v9 fallback · content slice 20 000 chars · coverage 95 % / diff 100 % / mutation
80 % · walking skeleton first · opaque credential + fingerprint-keyed caches, identity provider and
per-user credential source decided at M5/M6 · `ai` toolset without live AI in CI.

## 13. Left to the builder

Exact tool parameters and output fields within §5.5–§7; module split inside each layer; which
wire fields to model; concrete timeouts, retry counts and caps not fixed above; seeding data for
E2E; the precise ruff ignore list; how the Paperless OpenAPI schema is extracted from the image.

## 14. Risks

- Paperless releases monthly; API drift is caught by the weekly drift check and E2E against
  `latest`, not prevented.
- The drf-spectacular schema is not always exact; the schema guard may need documented,
  per-endpoint exceptions.
- `run_saved_view` requires re-implementing the frontend's filter-rule mapping (~40 rule types).
- Paperless AI quality and latency are outside this project's control; the ai toolset degrades
  to hidden when AI is disabled.
- Mutation testing can be slow; scope is limited to two packages and runs weekly.
