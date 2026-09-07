"""
Description: 提供与语义产品隔离的迁移任务、断点续跑与只读产物 API。
References: FastAPI、MigrationService、Version 0.1 迁移边界。
Referenced By: migration-web 启动入口和迁移应用前端。
"""

from __future__ import annotations

import os
import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from matlab_refactor_agent.interfaces.api.settings import env_file_path, install_model_settings_routes

from .jobs import MigrationJobManager


class MigrationRequest(BaseModel):
    """迁移任务请求；Web 可引用 5173 项目静态分析，旧调用仍可直接给目录。"""

    project_path: str | None = None
    semantic_index_reference: str | None = None
    analysis_job_id: str | None = None

    @model_validator(mode="after")
    def requires_project_or_analysis(self) -> "MigrationRequest":
        if not (self.project_path and self.project_path.strip()) and not self.analysis_job_id:
            raise ValueError("请选择项目静态分析，或填写 MATLAB 项目目录")
        return self


class MigrationCapabilities(BaseModel):
    """公开迁移子工程当前真实能力，避免把骨架误报为完成。"""

    application: str = "matlab-to-python-migration"
    version: str = "0.1.0"
    status: str = "ready"
    implemented: list[str] = Field(
        default_factory=lambda: [
            "wcc_reason_act_observation",
            "act_chunk_dag",
            "scc_atomic_boundaries",
            "shared_project_static_analysis",
            "optional_semantic_index",
            "isolated_python_assembly",
            "syntax_and_import_validation",
            "runnable_test_injection",
            "checkpoint_resume",
        ]
    )
    pending: list[str] = Field(
        default_factory=lambda: [
            "matlab_python_execution",
            "numerical_differential_validation",
            "project_publication",
        ]
    )


def create_app(frontend_dir: Path | None = None,
               manager: MigrationJobManager | None = None,
               env_file: Path | None = None) -> FastAPI:
    """入口只声明路由，后台执行与视图映射集中在 jobs.py。"""

    settings_file = env_file_path(env_file)
    jobs = manager or MigrationJobManager(env_file=settings_file)

    @asynccontextmanager
    async def lifespan(_api: FastAPI):
        yield
        await asyncio.to_thread(jobs.close)

    api = FastAPI(title="MATLAB to Python Migration API", version="0.1.0", lifespan=lifespan)
    install_model_settings_routes(api, settings_file)

    @api.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "application": "migration"}

    @api.get("/api/capabilities", response_model=MigrationCapabilities)
    def capabilities() -> MigrationCapabilities:
        return MigrationCapabilities()

    @api.post("/api/migrations", status_code=202)
    def create_migration(request: MigrationRequest) -> dict:
        return jobs.submit(
            request.project_path,
            request.semantic_index_reference,
            analysis_job_id=request.analysis_job_id,
        )

    @api.get("/api/project-analyses")
    def project_analyses() -> list[dict]:
        """返回可供迁移任务复用的 5173 项目静态分析。"""

        return jobs.analysis_projects()

    @api.get("/api/migrations")
    def list_migrations() -> list[dict]:
        return jobs.projects()

    @api.get("/api/migrations/{job_id}")
    def migration_status(job_id: str) -> dict:
        return jobs.status(job_id)

    @api.get("/api/migrations/{job_id}/heartbeat")
    async def migration_heartbeat(job_id: str) -> StreamingResponse:
        """SSE 单向推送轻量状态；正文只含计数，不含提示词或模型输出。"""

        jobs.status(job_id)  # 在开始流之前保留正常的 404 响应。

        async def stream():
            while True:
                status = await asyncio.to_thread(jobs.status, job_id)
                status["server_time"] = datetime.now(timezone.utc).isoformat()
                yield f"data: {json.dumps(status, ensure_ascii=False)}\n\n"
                if status["state"] not in {"queued", "running"}:
                    return
                await asyncio.sleep(0.5)

        return StreamingResponse(
            stream(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @api.post("/api/migrations/{job_id}/resume", status_code=202)
    def resume_migration(job_id: str) -> dict:
        return jobs.resume(job_id)

    @api.post("/api/migrations/{job_id}/restart", status_code=202)
    def restart_migration(job_id: str) -> dict:
        return jobs.restart(job_id)

    @api.get("/api/migrations/{job_id}/chains")
    def chains(job_id: str) -> list[dict]:
        return jobs.chains(job_id)

    @api.get("/api/migrations/{job_id}/chains/{chain_id}")
    def chain(job_id: str, chain_id: str) -> dict:
        return jobs.chain(job_id, chain_id)

    @api.get("/api/migrations/{job_id}/events")
    def events(job_id: str) -> list[dict]:
        return jobs.events(job_id)

    candidate = frontend_dir or (
        Path(os.environ["MATLAB_MIGRATION_FRONTEND_DIR"])
        if os.environ.get("MATLAB_MIGRATION_FRONTEND_DIR")
        else Path(__file__).resolve().parents[5] / "apps" / "migration" / "frontend" / "dist"
    )
    if candidate is not None:
        static_directory = candidate.expanduser().resolve()
        if (static_directory / "index.html").is_file():
            api.mount(
                "/",
                StaticFiles(directory=static_directory, html=True),
                name="migration-frontend",
            )

    return api


app = create_app()
