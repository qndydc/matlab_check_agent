# syntax=docker/dockerfile:1

FROM node:22-alpine AS frontend-builder
WORKDIR /build/frontend
RUN corepack enable && corepack prepare pnpm@11.22.0 --activate
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

FROM python:3.11-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MATLAB_REFACTOR_FRONTEND_DIR=/app/frontend/dist

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN python -m pip install --no-cache-dir .

COPY --from=frontend-builder /build/frontend/dist ./frontend/dist

RUN groupadd --system matlab-atlas \
    && useradd --system --gid matlab-atlas --home-dir /app matlab-atlas \
    && mkdir -p /data /projects \
    && chown -R matlab-atlas:matlab-atlas /app /data

USER matlab-atlas
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"]

CMD ["uvicorn", "matlab_refactor_agent.interfaces.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
