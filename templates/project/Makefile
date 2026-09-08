.DEFAULT_GOAL := help
-include .env
export
PLUGIN ?= $(shell ls -d ~/.claude/plugins/cache/ludvignion/harness-plugin/* 2>/dev/null | sort -V | tail -1)
SCRIPTS := $(PLUGIN)/scripts

# The venv lives outside the repo: on a Windows drvfs mount (/mnt/c) uv cannot
# create one in-tree — copying wheels fails with EPERM. Override to relocate.
UV_PROJECT_ENVIRONMENT ?= $(HOME)/.venvs/$(notdir $(CURDIR))
UV_LINK_MODE ?= copy

install:  ## Sync venv.
	uv sync

plugin:  ## Refresh the marketplace clone, then update the plugin.
	claude plugin marketplace update ludvignion
	claude plugin update harness-plugin@ludvignion

test:  ## Unit tests.
	uv run pytest tests --ignore=tests/evals -q

lint:  ## Lint + format check + types.
	uv run ruff check . && uv run ruff format --check . && uv run mypy src

complexity:  ## Simplicity gates (radon).
	uv run radon cc src -n C -s

mutation:  ## Mutation testing on critical modules (slow).
	uv run mutmut run

ci: lint test complexity  ## What CI runs.

eval:  ## Product evals (only if src/ calls a model). Costs money. Never in ci.
	uv run pytest tests/evals -q

board:  ## Render traces/board.html (the runner and the Stop hook do this too). Open it in a browser.
	python3 $(SCRIPTS)/render_board.py .

run:  ## Headless build→ci→verdict. Usage: make run T=1.2  or  make run P=1  (a plan, pausing at every Gate 2). From a session: /harness-plugin:runner
	@if [ -f .env ] && [ -z "$(OPIK_URL_OVERRIDE)" ] && [ "$(OPIK_DISABLE)" != "1" ]; then \
	  echo "OPIK_URL_OVERRIDE is empty in .env. Set it, or run with OPIK_DISABLE=1 to go untraced." >&2; exit 1; fi
	@if [ -z "$(T)$(P)" ]; then echo "usage: make run T=<id> | make run P=<plan>" >&2; exit 1; fi
	mkdir -p traces/runs && python3 $(SCRIPTS)/runner.py $(if $(P),--plan $(P),$(T)) 2>&1 | tee traces/runs/$(if $(P),plan-$(P),$(T)).log

approve:  ## Gate 1: approve a plan (status, approved:, [human] Log entry, commit). Usage: make approve N=1
	@if [ -z "$(N)" ]; then echo "usage: make approve N=<plan>" >&2; exit 1; fi
	python3 $(SCRIPTS)/kanban_ops.py approve $(N) --who "$$(git config user.name)"

gate:  ## Gate 2 in words. Usage: make gate A="ship 1.2" | A="reject 1.2 <reason>" | A="child 1.2 F1" | A="home 1.2 F2 1.3" | A="waive 1.2 F3 <reason>"
	@if [ -z "$(A)" ]; then echo 'usage: make gate A="ship <id>"' >&2; exit 1; fi
	python3 $(SCRIPTS)/kanban_ops.py $(A) --who "$$(git config user.name)"

verdict:  ## Render gate-2 page. Usage: make verdict T=1.2
	python3 $(SCRIPTS)/render_verdict.py $(T)

digest:  ## Rebuild traces/blind-spots.md from grill-misses.
	python3 $(SCRIPTS)/grill_digest.py

replay:  ## Re-run grill on golden briefs; diff escalations.
	python3 $(SCRIPTS)/replay.py .

loop:  ## Renamed. Use make run.
	@echo "use make run" >&2; exit 1

help:  ## Show this help.
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*##' '{printf "  %-12s %s\n", $$1, $$2}'

.PHONY: install plugin test lint complexity mutation ci eval board approve run gate verdict digest replay loop help
