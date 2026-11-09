_default:
    @just --list

# Install or update the development environment.
install:
    uv sync

# Lint: ruff, then the suppression and patching rules ruff cannot express.
lint: && hygiene
    uv run ruff check .

# The only suppressions are `# noqa: S101 -- why` and `# pyright: ignore[reportAny] -- why`:
# a specific code and a reason. Unused ones fail ruff (RUF100) and basedpyright. Not used at all:
# `# type: ignore` (blanket in pyright), `# pragma` (coverage exclusions are reviewed patterns
# in pyproject.toml), and file or range directives (`# ruff:`, `# flake8:`, `# isort:`):
# per-file exceptions belong in pyproject.toml with a reason.
# Patching (monkeypatch.setattr, setattr & co.) is banned: the only test seams are constructor
# injection and the HTTP transport.
hygiene:
    #!/usr/bin/env bash
    set -euo pipefail
    # git grep exits 1 when nothing matches; any other failure must fail the gate, not pass it.
    search() {
        local status=0
        git grep --untracked -nI "$@" -- '*.py' ':(exclude)docs/vendor' || status=$?
        if (( status > 1 )); then
            echo "hygiene: git grep failed with exit code $status" >&2
            exit "$status"
        fi
    }
    directive='#[[:space:]]*(noqa|ruff:|flake8|isort|type:[[:space:]]*ignore|pyright:|pragma)[^#]*'
    valid='^[^:]+:[0-9]+:# (noqa: [A-Z]+[0-9]+(, [A-Z]+[0-9]+)*|pyright: ignore\[[A-Za-z]+(, [A-Za-z]+)*\]) -- [^[:space:]]'
    directives="$(search -ioE "$directive")"
    bad=""
    while IFS= read -r line; do
        [[ -z "$line" || "$line" =~ $valid ]] || bad+="$line"$'\n'
    done <<< "$directives"
    patching="$(search -E '(^|[^[:alnum:]_])(setattr|delattr|setitem|delitem)\(')"
    status=0
    if [[ -n "$bad" ]]; then
        printf 'Suppression not in the form `# noqa: CODE -- reason` / `# pyright: ignore[rule] -- reason`:\n%s\n' "$bad"
        status=1
    fi
    if [[ -n "$patching" ]]; then
        printf 'Patching is banned; inject a fake through the constructor or the HTTP transport:\n%s\n\n' "$patching"
        status=1
    fi
    exit "$status"

# Format with ruff.
format:
    uv run ruff format .

# Verify formatting without writing.
format-check:
    uv run ruff format --check .

# Type-check src and tests (basedpyright, see pyproject.toml for the mode). basedpyright would
# silently accept every error recorded in a baseline file, so none may exist.
typecheck:
    @if [ -e .basedpyright/baseline.json ]; then echo "typecheck: remove .basedpyright/baseline.json; fix the errors or suppress each with a reason" >&2; exit 1; fi
    uv run basedpyright

# Architecture contracts (design §5.1).
imports:
    uv run lint-imports --no-logo

# Dependency hygiene: declare what you import, import what you declare.
deps:
    uv run deptry src

# Run the tests with branch coverage (fails below 95 %); writes coverage.xml for cov-diff.
test:
    uv run pytest --cov --cov-report=term-missing --cov-report=xml

# Require 100 % coverage on the lines changed against `base` (CI passes origin/main).
cov-diff base="main": test
    uv run diff-cover coverage.xml --compare-branch={{base}} --fail-under=100 --include-untracked

# Fail if uv.lock does not match pyproject.toml. Runs first in `check`: `uv run` re-locks.
lock-check:
    uv lock --check

# Scan the whole git history for secrets (gitleaks, pinned and installed by pre-commit). The
# commit hook only sees staged changes.
secrets:
    uv run pre-commit run gitleaks-history --hook-stage manual --all-files

# The full local gate; CI's check job runs the same recipes. `audit` needs the network.
check: lock-check lint format-check typecheck imports deps secrets cov-diff

# Audit the locked dependencies for known vulnerabilities. Triaged false positives are
# suppressed by ID in .pip-audit-ignore.
audit:
    #!/usr/bin/env bash
    set -euo pipefail
    requirements="$(mktemp)"
    trap 'rm -f "$requirements"' EXIT
    uv export --frozen --no-emit-project --all-groups \
        --format requirements-txt --output-file "$requirements" --quiet
    ignore=()
    while read -r id; do
        case "$id" in ''|\#*) continue ;; esac
        ignore+=("--ignore-vuln=$id")
    done < .pip-audit-ignore
    # The export pins and hashes every transitive dependency: audit it as-is, without resolving.
    # (`${ignore[@]+...}`: an empty array is "unbound" under `set -u` in macOS's bash 3.2.)
    uvx pip-audit==2.10.1 --strict --disable-pip --requirement "$requirements" ${ignore[@]+"${ignore[@]}"}

# Run every pre-commit hook on all files.
precommit:
    uv run pre-commit run --all-files
