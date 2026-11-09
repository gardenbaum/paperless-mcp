_default:
    @just --list

# Install or update the development environment.
install:
    uv sync

# Lint: ruff, then the suppression and patching rules ruff cannot express.
lint: && hygiene
    uv run ruff check .

# Suppressions name a specific code and a reason: `# noqa: S101 -- why`,
# `# pyright: ignore[reportAny] -- why`, `# pragma: no cover -- why`. `# type: ignore` (blanket
# in pyright) and `# ruff:` file or range directives are not used; per-file exceptions belong in
# pyproject.toml with a reason. Unused suppressions fail ruff (RUF100) and basedpyright.
# Patching (monkeypatch.setattr & co.) is banned: the only test seams are constructor injection
# and the HTTP transport.
hygiene:
    #!/usr/bin/env bash
    set -euo pipefail
    files=(-- '*.py' ':(exclude)docs/vendor')
    directive='#[[:space:]]*(noqa|ruff:|type:[[:space:]]*ignore|pyright:|pragma:[[:space:]]*no[[:space:]]*(cover|branch))[^#]*'
    valid='^[^:]+:[0-9]+:# (noqa: [A-Z]+[0-9]+(, [A-Z]+[0-9]+)*|pyright: ignore\[[A-Za-z]+(, [A-Za-z]+)*\]|pragma: no (cover|branch)) -- [^[:space:]]'
    status=0
    if bad="$(git grep --untracked -nIioE "$directive" "${files[@]}" | grep -vE "$valid")"; then
        printf 'Suppression without a specific code and a reason:\n%s\n\n' "$bad"
        status=1
    fi
    if bad="$(git grep --untracked -nIE '\.(setattr|delattr|setitem|delitem)\(' "${files[@]}")"; then
        printf 'Patching is banned; inject a fake through the constructor or the HTTP transport:\n%s\n\n' "$bad"
        status=1
    fi
    exit "$status"

# Format with ruff.
format:
    uv run ruff format .

# Verify formatting without writing.
format-check:
    uv run ruff format --check .

# Type-check src and tests (basedpyright, see pyproject.toml for the mode).
typecheck:
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

# The full local gate; CI's check job runs the same recipes. `audit` needs the network.
check: lint format-check typecheck imports deps cov-diff

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
    uvx pip-audit --strict --disable-pip --requirement "$requirements" ${ignore[@]+"${ignore[@]}"}

# Run every pre-commit hook on all files.
precommit:
    uv run pre-commit run --all-files
