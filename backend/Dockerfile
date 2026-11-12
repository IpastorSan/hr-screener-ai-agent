# syntax=docker/dockerfile:1.7
# The API: FastAPI on uvicorn, one process (in-process rate limits and SQLite).
# The retrieval model and the corpus vectors are baked in at build time, so production
# downloads nothing (HF_HUB_OFFLINE=1).

FROM ghcr.io/astral-sh/uv:0.9.13 AS uv

FROM python:3.13.9-slim-bookworm AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src
# Dependencies first: this layer only changes with the lockfile.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable
# Embedding model and vectors for the FAQ and the company documents.
ENV SAZON_CACHE_DIR=/app/cache
RUN /app/.venv/bin/sazon rag-index && chmod -R a+rX /app/cache

FROM python:3.13.9-slim-bookworm AS runtime
# uid 1000: the deploy user's uid on the VM, so the bind-mounted data directory is writable.
RUN groupadd --gid 1000 sazon \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin sazon \
    && install -d -o sazon -g sazon /data /data/backups
COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/cache /app/cache
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYDANTIC_AI_NO_BANNER=1 \
    SAZON_CACHE_DIR=/app/cache \
    HF_HUB_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    DATABASE_URL=sqlite:////data/sazon.db \
    TZ=UTC
WORKDIR /app
USER sazon
EXPOSE 8000
# Migrations run in the app's startup (sazon.storage.migrate); `sazon migrate` does it by hand.
CMD ["uvicorn", "--factory", "sazon.api.app:create_app", "--host", "0.0.0.0", "--port", "8000", \
     "--timeout-keep-alive", "75", "--no-server-header"]
