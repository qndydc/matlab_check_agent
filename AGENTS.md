# Repository Guidelines

## Project Structure & Module Organization

Python source lives under `src/matlab_refactor_agent/`. Keep rules in `domain/`, workflows in `orchestration/`, deterministic parsing and graph work in `workers/`, LLM behavior in `agents/`, and entry points in `interfaces/`. FastAPI is in `interfaces/api/`; the CLI is in `interfaces/cli/`.

The React/Vite application is under `frontend/src/`. Tests are split between `tests/unit/` and `tests/integration/`; small MATLAB samples belong in `tests/fixtures/matlab_projects/basic/`. Generated artifacts belong in ignored directories such as `var/`, `dist/`, and `node_modules/`.

## Build, Test, and Development Commands

- `python -m pip install -e ".[dev]"` installs the Python package and test tools.
- `python -m pytest` runs the complete test suite.
- `python -m pytest --cov=matlab_refactor_agent --cov-report=term-missing` reports coverage.
- `matlab-refactor analyze D:\path\to\project` analyzes a MATLAB repository from the CLI.
- `python scripts/start_mvp.py` starts the local API and Vite development UI.
- `cd frontend && pnpm install` installs frontend dependencies.
- `cd frontend && pnpm build` type-checks and creates the production bundle.

## Coding Style & Naming Conventions

Use four spaces in Python and two spaces in TypeScript/TSX. Follow PEP 8 naming: `snake_case` for functions and modules, `PascalCase` for classes, and uppercase names for constants. React components and TypeScript interfaces use `PascalCase`; hooks and functions use `camelCase`.

Prefer typed interfaces, Pydantic boundary models, and small deterministic functions. Keep LLM suggestions separate from validation and filesystem mutation. No formatter is configured; preserve surrounding style and run `git diff --check` before committing.

## Testing Guidelines

Pytest discovers `test_*.py` files and `test_*` functions. Add unit tests for the relevant subsystem and integration tests for CLI, API, persistence, or orchestration flows. Use temporary directories for filesystem tests. Bug fixes should include regression tests; new behavior should cover success, failure, and unresolved-call cases.

## Commit & Pull Request Guidelines

History uses Conventional Commit-style subjects, for example `feat: complete web MVP and refactor workflow`. Use a concise `type: summary` subject such as `fix: preserve graph on annotation failure`.

Pull requests should explain the problem, implementation, validation commands, and compatibility or safety implications. Link related issues and include screenshots for graph or UI changes. Never commit `.env`, API keys, SQLite files, generated output, or dependency directories.

## Safety & Configuration

Treat analyzed MATLAB repositories as read-only. Put secrets only in a local `.env`; keep `.env.example` limited to placeholders. Refactoring output must remain isolated from the input project and must not overwrite historical results.
