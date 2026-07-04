.PHONY: install lint format typecheck security complexity docs-lint docs-build docs-serve test coverage check clean

PYTHON ?= python3
UV ?= uv
VENV ?= .venv
BIN = $(VENV)/bin

# Prefer uv when available; fall back to venv + pip.
HAS_UV := $(shell command -v $(UV) >/dev/null 2>&1 && echo 1)

ifeq ($(HAS_UV),1)
INSTALL = $(UV) sync --extra dev --extra docs
RUN = $(UV) run
else
INSTALL = $(PYTHON) -m venv $(VENV) && $(BIN)/pip install -e ".[dev,docs]"
RUN = $(BIN)/
endif

install:
	$(INSTALL)

lint:
	$(RUN) ruff check .
	$(RUN) ruff format --check .

format:
	$(RUN) ruff format .
	$(RUN) ruff check --fix .

typecheck:
	$(RUN) mypy

security:
	$(RUN) bandit -r src -c pyproject.toml
	$(RUN) pip-audit

complexity:
	$(RUN) radon cc src -a -nc
	$(RUN) xenon --max-absolute B --max-modules B --max-average B src

docs-lint:
	npx --yes markdownlint-cli2 "**/*.md" "#site" "#.venv"
	lychee --offline --no-progress --exclude-path site --exclude mailto: \
		README.md PLAN.md wayfinder-cli-user-guide.md wayfinder-interaction-protocol-v0.1.md docs/

docs-build:
	$(RUN) mkdocs build

docs-serve:
	$(RUN) mkdocs serve

test:
	$(RUN) pytest -n auto

coverage:
	$(RUN) pytest -n auto --cov=wayfinder --cov-report=term-missing --cov-report=xml --cov-report=html \
		--cov-fail-under=80

check: lint typecheck security complexity docs-lint test coverage docs-build

clean:
	rm -rf site .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage coverage.xml
	rm -rf docs/index.md docs/wayfinder-cli-user-guide.md docs/wayfinder-interaction-protocol-v0.1.md
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
