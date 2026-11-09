# FastMCP 4 reading guide (relevance triage for paperless-mcp)

Read this before any FastMCP work, then open the specific vendored page. The vendored docs
(`docs/vendor/fastmcp/docs/`, marker in `docs/vendor/fastmcp/UPSTREAM`) are the source of truth:
FastMCP 4 (MCP SDK v2) is newer than most training data, so do not answer from memory.
Refresh with `just docs-sync`. Paths below are relative to `docs/vendor/fastmcp/docs/`.

**This project:** an MCP server for paperless-ngx 3.x. Local use over stdio first, later
Streamable HTTP as a remote connector for claude.ai (OAuth/OIDC proxy), then per-principal
Paperless credentials. Design: `backlog/docs/doc-1 - paperless-mcp-design.md`.

**FastMCP 4 in one paragraph:** built on MCP SDK v2. One server serves both protocol eras: the
session-based handshake era (up to 2025-11-25, what most clients speak today) and the sessionless
`2026-07-28` era (the default of `fastmcp.Client`). SDK v2 model fields are snake_case in Python
(`read_only_hint`, `input_schema`) while the wire stays camelCase. On the sessionless era
`ctx.elicit()` raises, `ctx.set_state` does not persist across calls and
`Middleware.on_initialize` never runs; `ctx.sample` and `ctx.list_roots` are gone. FastMCP's own
HTTP stack is httpx2.

**What is vendored:** the `docs/` subtree of the latest stable tag. `just docs-sync` prunes legacy
`v2/` and `v3/`, FastMCP's contributor docs (`development/{contributing,releases,tests}.mdx`) and
all images and videos (pages may link to media that is absent). Ignore `assets/`, `css/`,
`public/`, `snippets/`, `*.js` and `*.json` (site files).

---

## Core: read first

- `getting-started/whats-new.mdx` and `getting-started/upgrading/from-fastmcp-3.mdx`: every v3 to v4 break.
- `servers/server.mdx`: the `FastMCP` instance, including `mask_error_details`.
- `servers/tools.mdx`: **the most important page.** Tool parameters and descriptions, annotations,
  `tags`, structured output and output schemas (typed return models), `ToolError` versus masked
  errors, docstring parsing.
- `servers/context.mdx`: `Context` (logging, progress, request info).
- `servers/testing.mdx`: in-memory `Client` against the server object, no network. This is how
  tool tests are written here.
- `servers/providers/overview.mdx` and `local.mdx`: the Provider model.
- `servers/visibility.mdx`: enable/disable components by tag. Toolset selection and read-only
  mode are built on tags plus visibility.
- `servers/lifespan.mdx` and `servers/dependency-injection.mdx`: startup/shutdown (HTTP client,
  caches) and how dependencies reach tools.
- `servers/middleware.mdx`: cross-cutting concerns (request id, error boundary, timing).

## Remote use and auth (later milestones, read before PLMCP-34 to 37)

- `deployment/http.mdx`, `deployment/server-configuration.mdx`, `deployment/running-server.mdx`:
  Streamable HTTP, host/port/path, running behind a proxy.
- `servers/auth/authentication.mdx`: the decision spectrum.
- `servers/auth/token-verification.mdx`: server as a pure resource server validating tokens (`JWTVerifier`).
- `servers/auth/oidc-proxy.mdx` and `servers/auth/oauth-proxy.mdx`: the claude.ai connector path.
  claude.ai's redirect URI (`https://claude.ai/api/mcp/auth_callback`) appears in
  `oauth-proxy.mdx` (redirect URI validation); the same page covers **CIMD** (client ID metadata
  documents, `client_id` as an HTTPS URL), PKCE forwarding, key/storage management and the
  production checklist. The OIDC proxy inherits all of it.
- `servers/auth/remote-oauth.mdx` and `servers/auth/multi-auth.mdx`: validate-only in front of an
  IdP, and combining schemes.
- `servers/authorization.mdx`: scope checks beyond authentication.
- `servers/storage-backends.mdx`: where OAuth state lives (matters for restarts and multiple replicas).
- `servers/sessions.mdx`: per-user state that survives the sessionless era (use it instead of cross-call `ctx.set_state`).
- `clients/auth/bearer.mdx`, `clients/auth/oauth.mdx`, `clients/auth/cimd.mdx`: the client side, for tests and E2E.

> MCP auth moves fast. Check the vendored page, not memory.

## Sessionless-era caveats

- `ctx.elicit()` **raises** on sessionless (2026-07-28) connections, and the project forbids
  elicitation for confirmation anyway (destructive tools take `confirm: bool = False`).
  `servers/elicitation.mdx` explains the era gate and the guard pattern; read it only to understand
  the failure, not to use it.
- Do not keep state between calls in `ctx.set_state`; use explicit parameters, lifespan-owned
  objects or `servers/sessions.mdx`.
- `Middleware.on_initialize` never runs on sessionless connections; do not hang setup on it.
- `ctx.sample` is gone; the server does not call LLMs through the client.
- `servers/tasks.mdx` and `servers/extensions.mdx`: background tasks are an extension in v4. Not
  used; Paperless tasks are polled through `get_task`.

## Important: read when the topic comes up

- `servers/logging.mdx` and `servers/progress.mdx`: logging goes to stderr only.
- `servers/resources.mdx` and `servers/prompts.mdx`: the other component types (prompts arrive in PLMCP-33).
- `servers/pagination.mdx`: MCP-level list pagination (not Paperless paging).
- `servers/transforms/tool-search.mdx`, `code-mode.mdx`, `tool-transformation.mdx`: only if the
  tool budget is ever exceeded.
- `servers/tool-fingerprinting.mdx`, `servers/versioning.mdx`, `servers/icons.mdx`, `servers/telemetry.mdx`: specialist.
- `clients/client.mdx` and `clients/transports.mdx` (v4: `mode="auto"` versus `mode="legacy"`),
  `clients/tools.mdx`: the in-memory and HTTP test clients.
- `cli/overview.mdx`, `cli/running.mdx`, `cli/inspecting.mdx`: dev workflow and inspection.
- `integrations/claude-code.mdx`, `integrations/claude-desktop.mdx`, `integrations/mcp-json-configuration.mdx`: client setup for the README.
- `more/settings.mdx`, `more/faq.mdx`: reference (settings include the error-masking switch).
- `python-sdk/`: generated API reference. Do not read through; look up a symbol when needed.

## Skip

- `v2/` and `v3/` (pruned) and `changelog.mdx`, `updates.mdx`, `community/`, `patterns/contrib.mdx`.
- `apps/` (interactive UIs), `servers/composition.mdx`, `servers/providers/{proxy,filesystem,skills,custom}.mdx`, `servers/sampling.mdx`.
- `integrations/openapi.mdx`, `integrations/fastapi.mdx`, `tutorials/rest-api.mdx`: generating tools from a REST API. Tools here are hand-designed.
- `servers/auth/full-oauth-server.mdx`: only if this server became its own identity provider.
- Other `integrations/*` (IdPs and vendors): read only the one matching a service actually used.
- `deployment/{prefect-horizon,sandboxed-agents}.mdx`, `getting-started/upgrading/from-{fastmcp-2,mcp-sdk-v1,mcp-sdk-v2,low-level-sdk-v1,low-level-sdk-v2}.mdx`.
