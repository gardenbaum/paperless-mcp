# paperless-mcp

An [MCP](https://modelcontextprotocol.io) server for [paperless-ngx](https://docs.paperless-ngx.com)
3.x, built on FastMCP 4.

**Status:** early development. One tool so far, `get_instance_overview`; the plan lives in
`backlog/` (Backlog.md). Requires paperless-ngx 3.x (API version 10); 2.x is not supported.

## Use with Claude Code

Create an API token in Paperless (profile menu, "My Profile", API auth token), then register the
server over stdio from a clone of this repository:

```sh
uv sync
claude mcp add paperless \
    --env PAPERLESS_URL=https://paperless.example.org \
    --env PAPERLESS_TOKEN=your-api-token \
    -- "$PWD/.venv/bin/paperless-mcp"
```

Add `--scope user` to use it in every project. Check it with `claude mcp list` or `/mcp` in
Claude Code, then ask for an overview of your Paperless instance. `python -m paperless_mcp` is
the same entrypoint.

At startup the server checks the connection: a rejected token or a Paperless that does not speak
API version 10 stops it with a message on stderr (in Claude Code: `claude --debug` or the `/mcp`
details). An unreachable Paperless does not stop it; the tools report the problem until
Paperless is back. Logs go to stderr (the server's own records as JSON lines) and never contain
tokens, document content or query text at INFO or above: tool arguments and error messages are
not logged, and an internal error is logged by type, with its traceback only at DEBUG.

## Configuration

| Variable | Meaning |
|---|---|
| `PAPERLESS_URL` | Base URL of the Paperless instance (required) |
| `PAPERLESS_TOKEN` | Paperless API token (required) |
| `PAPERLESS_PUBLIC_URL` | Base URL for links shown to users, if it differs from `PAPERLESS_URL` |
| `PAPERLESS_MCP_LOG_LEVEL` | `DEBUG`, `INFO` (default), `WARNING`, `ERROR` or `CRITICAL` |
| `PAPERLESS_MCP_STARTUP_TIMEOUT` | Seconds the startup check may take (default 5) |

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
