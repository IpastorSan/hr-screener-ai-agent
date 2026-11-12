# Contributing

## Layout
- `backend/` – Python package `sazon` (FastAPI, uv). Source in `backend/src/sazon/`, tests in `backend/tests/`.
- `web/` – Next.js: careers pages, the candidate chat and voice page, the recruiter dashboard.
- `evals/` – conversation evals and model benchmarks.
- `docs/` – design notes.

## Architecture rules
- The screening flow lives in `sazon/engine/` as deterministic Python. The engine decides what happens next; models only extract, phrase and summarize.
- `engine/` never imports an LLM SDK. All model access goes through `sazon/llm/`.
- Every LLM call goes through OpenRouter with zero data retention and `data_collection: deny` enforced, always: there is no setting to turn it off. The base URL is configurable (`eu.openrouter.ai` for EU in-region routing). Do not add direct provider clients.
- Configuration comes from `sazon.config.get_settings()`; see `backend/.env.example`.

## Commands
- `make check` – ruff lint, ruff format check, pyright and pytest, then the web's lint, typecheck, unit tests (`npm test`, `node --test` over `src/**/*.test.ts`) and build. Must pass before a change is done.
- `make` (or `make help`) – list the targets.
- `make fmt` – apply ruff fixes and formatting.
- `make mutation-audit` – pre-release check that the tests catch 86 deliberate bugs in the rules that matter (`backend/scripts/mutation_audit.py`; `CHECK=1` checks the anchors, `ONLY=W1,Y14` runs some mutants). About 2 minutes; not part of `make check`.
- `make replay` – re-record the engine replay corpus (`evals/replay/corpus.jsonl.gz`: every engine call of the offline journeys and e2e personas, about 20 seconds) and `docs/engine/state-census.md`. `make check` replays the corpus and fails on any change in what the engine returns: regenerate only when the engine's behaviour changes on purpose, and review the census diff.
- Eval tests alone: `cd backend && uv run pytest -c pyproject.toml ../evals` (without `-c`, pytest takes the repo root as rootdir, misses the asyncio settings and fails the async tests).
- `make dev-api` / `make dev-web` – run the API or the dashboard locally. `make demo` – a reset, seeded demo on its own database (offline without an OpenRouter key).
- Python deps: `cd backend && uv add <pkg>` (or `uv add --dev`).
- Schema changes need an Alembic revision: `cd backend && uv run alembic revision --autogenerate -m "..."` (SQLite batch mode, must also run on Postgres). `test_migrations` fails while the models and the migrations differ.
- `make stack-up` / `make stack-down` – the deploy stack (Caddy, API, web) on http://localhost:8080; see `deploy/README.md`.
- `make obs-up` / `make obs-down` – the same stack plus Langfuse (http://localhost:3100), Prometheus, Loki and Grafana (http://localhost:3200).
- `uv run --project backend python -m evals.model_selection --stage extraction|reply|judge|report --models a/b,c/d@low [--cases N] [--dry-run]` – model benchmark through the production LLM layer, from the repo root (needs `MAX_EVAL_USD`; see `evals/results/model_selection.md`). `MAX_EVAL_USD` caps the cumulative spend in the local, untracked ledger `evals/results/cost_ledger.jsonl`, not one run: raise it, or move the ledger aside, when it is reached.
- `uv run --project backend python -m evals.e2e --combo A|B --personas all --repeat 1 [--dry-run] [--offline]` – end-to-end persona evals against the real service (simulated candidate, deterministic checks, judge); results in `evals/results/e2e.md`. `--offline` needs no model. `python -m evals.e2e.export_samples` writes `docs/sample-conversations/`.
- `uv run --project backend python -m evals.journeys [--agent a] [--group g] [--id j] [--offline] [--repeat N] [--dry-run] [--keep-db]` (or `make journeys`, offline) – scripted multi-session candidate journeys (`evals/journeys/cases/*.yaml` for riders, `cases/<agent>/*.yaml` for another agent) with per-step expectations against the real service; reports in `evals/results/journeys/<timestamp>/` (untracked). Format: `evals/journeys/README.md`.

## Git
- Branches: `{type}/{short-description}`, e.g. `feat/intake-flow`.
- Commit messages: imperative, concise. No co-author trailers or "generated with" lines.
