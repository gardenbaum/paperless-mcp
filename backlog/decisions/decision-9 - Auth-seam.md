---
id: decision-9
title: Auth seam
date: '2026-11-09T20:44:00Z'
status: accepted
---
## Context

The server must work today over stdio for Claude Code and Desktop, later over HTTP for claude.ai, and eventually for several users with their own Paperless credentials. Which identity provider fronts claude.ai and where per-user Paperless credentials come from are not known yet. Building either now would be speculation; ignoring the seam would force a rewrite below the composition root later.

## Decision

- Now: stdio with a static Paperless token (`PAPERLESS_TOKEN`, `SecretStr`); logs go to stderr only.
- HTTP (Streamable HTTP via `app.py`) is secure by default: it refuses to start without an auth configuration unless explicitly bound to loopback with `allow_unauthenticated_loopback=true`. First auth mode: static bearer token(s) for the MCP endpoint, plus a host allowlist and request-id middleware.
- The adapter obtains credentials only from a `CredentialProvider` seam returning an opaque `PaperlessCredential` (auth header material plus a stable, non-secret fingerprint). It never reads settings or globals.
- Deferred on purpose, to be decided as new Backlog decisions at the start of their milestone: the identity provider for the claude.ai connector (PLMCP-36, FastMCP `OIDCProxy`/`OAuthProxy`) and the source of per-principal Paperless credentials (PLMCP-37: admin-kept mapping, user-supplied tokens or a trusted upstream header).

## Consequences

- Neither deferred answer changes code below `server.py` and the credential provider.
- Caches are keyed by the credential fingerprint.
- Paperless enforces permissions per user; the server adds none of its own.

## Enforcement

- Startup tests: HTTP refuses to start without auth unless loopback is explicitly allowed (PLMCP-34); stdio and static-token startup (PLMCP-8).
- Import contracts "No pydantic-settings below the composition root (configuration is injected)" and "Only the composition root imports tools and paperless" keep the seam intact.
- Redaction tests for tokens and auth headers in logs and exceptions (PLMCP-6).
- The deferred choices are enforced by process: PLMCP-36 and PLMCP-37 must start with a decision (manual review: the choice cannot be tested before it exists).
