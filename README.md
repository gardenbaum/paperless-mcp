# paperless-mcp

An [MCP](https://modelcontextprotocol.io) server for [paperless-ngx](https://docs.paperless-ngx.com)
3.x, built on FastMCP 4.

**Status:** early development. There are no tools yet; the plan lives in `backlog/`
(Backlog.md).

## Configuration

| Variable | Meaning |
|---|---|
| `PAPERLESS_URL` | Base URL of the Paperless instance (required) |
| `PAPERLESS_TOKEN` | Paperless API token (required) |
| `PAPERLESS_PUBLIC_URL` | Base URL for links shown to users, if it differs from `PAPERLESS_URL` |
| `PAPERLESS_MCP_LOG_LEVEL` | `DEBUG`, `INFO` (default), `WARNING`, `ERROR` or `CRITICAL` |

## Development

Requires [uv](https://docs.astral.sh/uv/), [just](https://just.systems) and Go (pre-commit builds
the pinned gitleaks that `just check` runs over the whole history).

```sh
just install                  # create the environment
uv run pre-commit install     # run the gates on every commit
just check                    # the full local gate, identical to CI
just --list                   # all recipes
```

Lint suppressions name a specific code and a reason, e.g. `# noqa: S101 -- reason`
(see `just hygiene`). Inline `# pragma` comments are banned: coverage exclusions are reviewed
`exclude_also` patterns in `pyproject.toml`.

## Licence

MIT, see [LICENSE](LICENSE).
