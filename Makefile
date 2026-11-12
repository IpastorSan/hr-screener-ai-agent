.PHONY: help check check-backend check-web mutation-audit journeys replay diagram fmt dev-api dev-web demo stack-up stack-seed stack-down obs-up obs-down

help: ## List the targets
	@awk 'BEGIN {FS = ":.*## "} /^[a-z-]+:.*## / {printf "  %-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

check: check-backend check-web ## Everything CI runs: backend and web checks

# pytest runs in parallel; the two retrieval-model files share one worker (their
# xdist_group "embedding-model"), so the cached vectors are written once.
check-backend: ## ruff, ruff format check, pyright, pytest
	cd backend && uv run ruff check . ../evals
	cd backend && uv run ruff format --check . ../evals
	cd backend && uv run pyright
	cd backend && uv run pytest -n auto --dist loadgroup

check-web: ## eslint, tsc, unit tests, next build
	cd web && npm run lint
	cd web && npm run typecheck
	cd web && npm test
	cd web && npm run build

# Pre-release: each of 86 deliberate bugs patched into a copy of the backend must fail its tests.
mutation-audit: ## Check the tests catch 86 deliberate bugs (about 2 min)
	cd backend && uv run python scripts/mutation_audit.py

# Scripted candidate journeys (evals/journeys) against the real service. Offline by default;
# ARGS="--group seed" for real models (needs MAX_EVAL_USD), --id, --repeat, --keep-db.
JOURNEY_ARGS ?= --offline
journeys: ## Play every agent's candidate journeys in evals/journeys/cases (offline unless ARGS)
	uv run --project backend python -m evals.journeys $(or $(ARGS),$(JOURNEY_ARGS))

# The engine replay corpus (evals/replay) and docs/engine/state-census.md, recorded from the
# offline journeys and e2e personas. Only when the engine's behaviour changes on purpose: the
# replay test in `make check` fails on any change that was not.
replay: ## Re-record the engine replay corpus and the state census
	uv run --project backend python -m evals.replay.record

# docs/engine/states.mmd from the edges the engine's handlers, interrupts and entry points
# declare; `make check` fails while it differs from them.
diagram: ## Regenerate the engine's statechart (docs/engine/states.mmd)
	cd backend && uv run sazon engine-diagram ../docs/engine/states.mmd

fmt: ## Apply ruff fixes and formatting
	cd backend && uv run ruff check --fix . ../evals
	cd backend && uv run ruff format . ../evals

# JSON logs, also appended to backend/logs/dev-api.log, which `make obs-up` ships to Loki as
# service `api-dev` (its metrics too, as job `api-dev`). DEV_LOG_FORMAT=text for plain lines.
DEV_LOG_FORMAT ?= json

dev-api: ## The API with reload on :8000
	mkdir -p backend/logs
	cd backend && LOG_FORMAT=$(DEV_LOG_FORMAT) uv run uvicorn --factory sazon.api.app:create_app \
		--reload --port 8000 2>&1 | tee -a logs/dev-api.log

dev-web: ## The web dev server
	cd web && npm run dev

# A local demo on its own database (backend/demo.db), reset and seeded on every run: the API on
# DEMO_API_PORT, the web on DEMO_WEB_PORT, nudges after 9 and 45 seconds instead of 3 and 15
# minutes, and a fake calendar. With no OPENROUTER_API_KEY in backend/.env it runs offline
# (keyword extractor and template replies).
DEMO_API_PORT ?= 8000
DEMO_WEB_PORT ?= 3000
DEMO_NUDGE_SCALE ?= 0.05
DEMO_ENV = DATABASE_URL=sqlite:///./demo.db SCHEDULING_SOURCE=fake \
	WEB_ORIGIN=http://localhost:$(DEMO_WEB_PORT) \
	$(if $(shell grep -sE '^OPENROUTER_API_KEY=.+' backend/.env),,SAZON_FAKE_LLM=1)

demo: ## Reset, seeded local demo on its own database
	test -f backend/.env || cp backend/.env.example backend/.env
	cd web && { test -d node_modules || npm ci; }
	cd backend && uv run sazon rag-index
	cd backend && $(DEMO_ENV) uv run sazon migrate
	cd backend && $(DEMO_ENV) uv run sazon seed --reset --n 60
	@echo "Candidate chat: http://localhost:$(DEMO_WEB_PORT)/apply"
	@echo "Recruiters:     http://localhost:$(DEMO_WEB_PORT)/recruiters (accounts in backend/src/sazon/agents/riders/data/recruiters.json)"
	trap 'kill 0' INT TERM EXIT; \
	(cd backend && $(DEMO_ENV) uv run uvicorn --factory sazon.api.app:create_app \
		--port $(DEMO_API_PORT)) & \
	cd web && NEXT_PUBLIC_API_URL=http://localhost:$(DEMO_API_PORT) \
		NEXT_PUBLIC_NUDGE_SCALE=$(DEMO_NUDGE_SCALE) npm run dev -- --port $(DEMO_WEB_PORT)

# The deploy stack on localhost (http://localhost:8080), offline model, images built here.
STACK_PROJECT ?= sazon-local
STACK = docker compose -p $(STACK_PROJECT) --env-file deploy/local.env \
	-f deploy/compose.yaml -f deploy/compose.local.yaml

stack-up: ## The deploy stack on http://localhost:8080
	mkdir -p deploy/data-local/backups
	$(STACK) up -d --build --wait

stack-seed: ## Seed 40 candidates into the local stack
	$(STACK) exec -T api sazon seed --n 40 </dev/null

stack-down: ## Stop the local stack and delete its volumes
	$(STACK) down -v

# The same stack plus Langfuse, Prometheus, Loki and Grafana (deploy/observability.compose.yaml):
# Langfuse on http://localhost:3100, Grafana on http://localhost:3200.
OBS = $(STACK) --env-file deploy/observability.local.env \
	-f deploy/observability.compose.yaml -f deploy/observability.local.yaml

obs-up: ## The local stack plus Langfuse, Prometheus, Loki, Grafana
	mkdir -p deploy/data-local/backups backend/logs
	$(OBS) up -d --build --wait --wait-timeout 300

obs-down: ## Stop the observability stack and delete its volumes
	$(OBS) down -v
