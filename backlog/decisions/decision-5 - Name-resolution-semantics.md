---
id: decision-5
title: Name-resolution semantics
date: '2026-11-09T20:44:00Z'
status: accepted
---
## Context

LLM callers pass names ("Tax", "Invoice") where Paperless wants ids. A wrong silent guess attaches a document to the wrong correspondent or tag, and such errors are hard to notice later. Paperless itself offers no unique-name lookup across kinds.

## Decision

A `TaxonomyResolver` service resolves name-or-id inputs for every taxonomy kind (design 5.4):

- A numeric input is an id; otherwise an exact match, then a case- and accent-insensitive match.
- More than one match raises `Ambiguous` listing the candidates; no match raises `NotFound` with the closest suggestions.
- The resolver never auto-picks a fuzzy match. Auto-create exists only where a tool offers it explicitly (for example `create_missing_tags=true`).
- Backed by a per-credential cache with full pagination, TTL and invalidation on the server's own writes.

## Consequences

- The model sometimes needs a second call to disambiguate; accepted, the error names the candidates and the next step.
- The cache is keyed by the credential fingerprint, so multi-user mode (decision 9) cannot leak taxonomy between principals.
- Taxonomy names visible to a user are limited by that user's Paperless permissions.

## Enforcement

Unit and property tests (hypothesis) over `TaxonomyResolver` with `FakePaperless`: exact before insensitive, ambiguity is always an error and never resolved, no match never guesses, numeric input is an id, cache invalidation on writes. Added by PLMCP-9 (resolver and per-credential cache); the create/update taxonomy task PLMCP-19 extends them for explicit auto-create.
