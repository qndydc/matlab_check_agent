# syntax=docker/dockerfile:1

FROM node:22-alpine AS semantic-frontend-builder
WORKDIR /build/apps/semantic/frontend
RUN corepack enable && corepack prepare pnpm@11.22.0 --activate
COPY apps/semantic/frontend/package.json apps/semantic/frontend/pnpm-lock.yaml apps/semantic/frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY apps/semantic/frontend/ ./
COPY apps/shared/ ../../shared/
RUN pnpm build

FROM node:22-alpine AS migration-frontend-builder
WORKDIR /build/apps/migration/frontend
RUN corepack enable && corepack prepare pnpm@11.22.0 --activate
COPY apps/migration/frontend/package.json apps/migration/frontend/pnpm-lock.yaml apps/migration/frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY apps/migration/frontend/ ./
COPY apps/shared/ ../../shared/
RUN pnpm build

FROM python:3.11-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MATLAB_REFACTOR_FRONTEND_DIR=/app/apps/semantic/frontend/dist \
    MATLAB_MIGRATION_FRONTEND_DIR=/app/apps/migration/frontend/dist

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN python -m pip install --no-cache-dir .

COPY --from=semantic-frontend-builder /build/apps/semantic/frontend/dist ./apps/semantic/frontend/dist
COPY --from=migration-frontend-builder /build/apps/migration/frontend/dist ./apps/migration/frontend/dist

RUN groupadd --system --gid 10001 matlab-atlas \
    && useradd --system --uid 10001 --gid matlab-atlas --home-dir /app matlab-atlas \
    && mkdir -p /data /projects \
    && chown -R matlab-atlas:matlab-atlas /app /data

USER matlab-atlas
EXPOSE 8000 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"]

CMD ["python", "-m", "matlab_refactor_agent.apps.combined"]
