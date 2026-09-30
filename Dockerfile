FROM node:24-bookworm-slim AS frontend-build

RUN corepack enable
WORKDIR /workspace/frontend

COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY frontend/ ./
RUN pnpm build


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WEREWOLF_DM_DB_PATH=/app/data/werewolf_dm.sqlite3 \
    WEREWOLF_DM_STATIC_DIR=/app/static

WORKDIR /app

COPY LICENSE /app/backend/LICENSE
COPY backend/pyproject.toml backend/README.md /app/backend/
COPY backend/src /app/backend/src
RUN python -m pip install --no-cache-dir /app/backend

COPY --from=frontend-build /workspace/frontend/dist /app/static

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app

USER appuser
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"

CMD ["python", "-m", "uvicorn", "werewolf_dm.interfaces.http_ws.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
