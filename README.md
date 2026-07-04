# wayfinder

Unix command protocol for determining the next step to achieve a goal

## Status

Wayfinder is in early implementation. The protocol core library is implemented
under `src/wayfinder/core/`, with tests for canonical JSON, hash chains, event
logs, reducers, idempotency, artifacts, validation, freshness, and update
mapping.

The `wayfinder` CLI is intentionally still a Phase 3 stub. The examples in
`wayfinder-cli-user-guide.md` describe the intended user experience, but most
CLI commands are not runnable yet.

## Setup

Install development and documentation dependencies:

```sh
make install
```

The project prefers `uv` when available and falls back to a local virtual
environment plus `pip`.

## Common Commands

```sh
make fast       # quick pytest subset for local edits
make test       # full pytest suite
make coverage   # coverage report with threshold
make lint       # ruff check and format check
make format     # auto-format and ruff fixes
make typecheck  # mypy strict mode
make check      # full local quality gate
```

For one focused test run:

```sh
make single TEST=tests/core/test_reducer.py
```

## Documentation

- `wayfinder-interaction-protocol-v0.1.md` is the canonical protocol spec.
- `wayfinder-cli-user-guide.md` is the canonical guide for the planned CLI.
- `PLAN.md` tracks the phased implementation plan.

Build the documentation site with:

```sh
make docs-build
```

Serve it locally with:

```sh
make docs-serve
```
