# wayfinder

Unix command protocol for determining the next step to achieve a goal

## Status

Wayfinder implements the [Wayfinder Interaction Protocol v0.1](wayfinder-interaction-protocol-v0.1.md)
with a runnable CLI, executor, LLM layer, prose front-ends, and optional §9
machines. The protocol core lives under `src/wayfinder/core/`; entry points are
declared in `pyproject.toml`.

Quick start:

```sh
./setup.sh
wayfinder doctor
examples/02-quickstart/run.sh --scripted
```

Use `make examples-scripted` to run every guide example harness in deterministic
mode (what CI runs). Live LLM-backed workflows need endpoint configuration
described in [wayfinder-security.md](wayfinder-security.md).

## Setup

Install development and documentation dependencies:

```sh
make install
```

Or use the project setup script (creates config stubs and runs doctor):

```sh
./setup.sh          # full install with §9 machine extras
./setup.sh --minimal
```

The project prefers `uv` when available and falls back to a local virtual
environment plus `pip`.

## Common Commands

```sh
make fast               # quick pytest subset for local edits
make test               # full pytest suite
make examples-scripted  # all examples/*/run.sh --scripted
make coverage           # coverage report with threshold
make lint               # ruff check and format check
make format             # auto-format and ruff fixes
make typecheck          # mypy strict mode
make check              # full local quality gate
```

For one focused test run:

```sh
make single TEST=tests/core/test_reducer.py
```

## Documentation

- `wayfinder-interaction-protocol-v0.1.md` is the canonical protocol spec.
- `wayfinder-cli-user-guide.md` is the canonical guide for the CLI.
- `wayfinder-security.md` covers credentials, secrets, and policy.
- `PLAN.md` tracks the phased implementation plan.

Build the documentation site with:

```sh
make docs-build
```

Serve it locally with:

```sh
make docs-serve
```
