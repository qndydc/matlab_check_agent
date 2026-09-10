# syntax=docker/dockerfile:1

FROM node:22-alpine AS frontend-builder
WORKDIR /build/apps/semantic/frontend
RUN corepack enable && corepack prepare pnpm@11.22.0 --activate
COPY apps/semantic/frontend/package.json apps/semantic/frontend/pnpm-lock.yaml apps/semantic/frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY apps/semantic/frontend/ ./
COPY apps/shared/ ../../shared/
RUN pnpm build

FROM python:3.11-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MATLAB_REFACTOR_FRONTEND_DIR=/app/apps/semantic/frontend/dist

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN python -m pip install --no-cache-dir .

COPY --from=frontend-builder /build/apps/semantic/frontend/dist ./apps/semantic/frontend/dist

RUN groupadd --system --gid 10001 matlab-atlas \
    && useradd --system --uid 10001 --gid matlab-atlas --home-dir /app matlab-atlas \
    && mkdir -p /data /projects \
    && chown -R matlab-atlas:matlab-atlas /app /data

USER matlab-atlas
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"]

CMD ["uvicorn", "matlab_refactor_agent.apps.semantic.backend.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
