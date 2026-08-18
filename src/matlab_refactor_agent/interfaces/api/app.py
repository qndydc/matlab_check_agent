"""
Description: Provide asynchronous in-process jobs and REST endpoints for the Web MVP.
References: AnalysisService, GraphDocument, SemanticIndex, FastAPI.
Referenced By: interfaces.api.main, frontend, and API tests.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Annotated, Callable, Literal, cast
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from matlab_refactor_agent.application import AnalysisService
from matlab_refactor_agent.domain.semantics import SemanticIndex
from matlab_refactor_agent.infrastructure.config import load_settings
from matlab_refactor_agent.workers.graph_output import GraphDocument, build_graph_document
from matlab_refactor_agent.workers.graph_view import (
    GraphDirection,
    GraphScope,
    GraphViewDocument,
    build_graph_view,
)

from .storage import SQLiteWebProjectStore, StoredWebProject

JobState = Literal["queued", "running", "completed", "failed"]
JobStage = Literal["analyze", "annotate"]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AnalyzeRequest(BaseModel):
    project_path: str = Field(min_length=1)


class JobResponse(BaseModel):
    job_id: str
    project_path: str
    state: JobState
    stage: JobStage
    message: str
    error: str | None = None
    graph_ready: bool = False
    semantics_ready: bool = False
    created_at: datetime
    updated_at: datetime


@dataclass
class _Job:
    job_id: str
    project_path: Path
    state: JobState = "queued"
    stage: JobStage = "analyze"
    message: str = "等待分析"
    error: str | None = None
    graph: GraphDocument | None = None
    semantics: SemanticIndex | None = None
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)

    def response(self) -> JobResponse:
        """作用：将内部任务转换成可公开返回的 API 状态模型。"""

        return JobResponse(
            job_id=self.job_id,
            project_path=str(self.project_path),
            state=self.state,
            stage=self.stage,
            message=self.message,
            error=self.error,
            graph_ready=self.graph is not None,
            semantics_ready=self.semantics is not None,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    def stored(self) -> StoredWebProject:
        """作用：将内存任务转换成可写入 SQLite 的完整项目快照。"""

        return StoredWebProject(
            job_id=self.job_id,
            project_path=self.project_path,
            state=self.state,
            stage=self.stage,
            message=self.message,
            error=self.error,
            graph=self.graph,
            semantics=self.semantics,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    @classmethod
    def from_stored(cls, project: StoredWebProject) -> "_Job":
        """作用：把 SQLite 项目快照恢复成可继续操作的内存任务。"""

        return cls(
            job_id=project.job_id,
            project_path=project.project_path,
            state=cast(JobState, project.state),
            stage=cast(JobStage, project.stage),
            message=project.message,
            error=project.error,
            graph=project.graph,
            semantics=project.semantics,
            created_at=project.created_at,
            updated_at=project.updated_at,
        )


class MvpJobManager:
    """Small in-process job runner for the local single-user MVP."""

    def __init__(
        self,
        service_factory: Callable[[], AnalysisService] | None = None,
        executor: ThreadPoolExecutor | None = None,
        store: SQLiteWebProjectStore | None = None,
    ) -> None:
        settings = None
        if service_factory is None:
            settings = load_settings()
            self._service_factory = lambda configured=settings: AnalysisService(
                configured
            )
        else:
            self._service_factory = service_factory
        if store is None:
            settings = settings or load_settings()
            self._store = SQLiteWebProjectStore(settings.orchestrator.web_db)
        else:
            self._store = store
        self._executor = executor or ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="matlab-web"
        )
        self._jobs: dict[str, _Job] = {}
        self._lock = RLock()

    def submit_analysis(self, project_path: str) -> JobResponse:
        project = Path(project_path).expanduser().resolve()
        job = _Job(job_id=uuid4().hex, project_path=project)
        with self._lock:
            self._jobs[job.job_id] = job
            self._persist(job)
        self._submit(job, self._run_analysis)
        return job.response()

    def submit_annotation(self, job_id: str) -> JobResponse:
        job = self._get(job_id)
        with self._lock:
            if job.state in {"queued", "running"}:
                raise HTTPException(status.HTTP_409_CONFLICT, "任务仍在运行")
            if job.graph is None:
                raise HTTPException(status.HTTP_409_CONFLICT, "请先完成普通图分析")
            job.state = "queued"
            job.stage = "annotate"
            job.message = "等待生成三级注释"
            job.error = None
            job.updated_at = _utc_now()
            self._persist(job)
        self._submit(job, self._run_annotation)
        return job.response()

    def status(self, job_id: str) -> JobResponse:
        with self._lock:
            return self._get(job_id).response()

    def graph(self, job_id: str) -> GraphDocument:
        with self._lock:
            graph = self._get(job_id).graph
            if graph is None:
                raise HTTPException(status.HTTP_409_CONFLICT, "图数据尚未生成")
            return graph

    def graph_view(
        self,
        job_id: str,
        *,
        scope: GraphScope,
        focus_id: str | None,
        depth: int,
        limit: int,
        direction: GraphDirection,
        query: str | None,
    ) -> GraphViewDocument:
        """作用：从持久化完整图即时生成受节点上限约束的渐进浏览视图。"""

        with self._lock:
            job = self._get(job_id)
            if job.graph is None:
                raise HTTPException(status.HTTP_409_CONFLICT, "图数据尚未生成")
            summaries = {
                item.file_path.replace("\\", "/"): item.role
                for item in (job.semantics.files if job.semantics else [])
            }
            try:
                return build_graph_view(
                    job.graph,
                    scope=scope,
                    focus_id=focus_id,
                    depth=depth,
                    limit=limit,
                    direction=direction,
                    query=query,
                    file_summaries=summaries,
                )
            except ValueError as error:
                raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error

    def semantics(self, job_id: str) -> SemanticIndex:
        with self._lock:
            semantics = self._get(job_id).semantics
            if semantics is None:
                raise HTTPException(status.HTTP_409_CONFLICT, "三级注释尚未生成")
            return semantics

    def projects(self) -> list[JobResponse]:
        """作用：列出所有持久化项目，供前端历史记录面板展示。"""

        return [_Job.from_stored(item).response() for item in self._store.list()]

    def delete_project(self, job_id: str) -> None:
        """作用：删除非运行中项目的本地图和三级语义快照。"""

        with self._lock:
            job = self._get(job_id)
            if job.state in {"queued", "running"}:
                raise HTTPException(status.HTTP_409_CONFLICT, "运行中的项目不能删除")
            if not self._store.delete(job_id):
                raise HTTPException(status.HTTP_404_NOT_FOUND, "项目不存在")
            self._jobs.pop(job_id, None)

    def _submit(self, job: _Job, operation: Callable[[_Job], None]) -> None:
        future = self._executor.submit(operation, job)
        future.add_done_callback(lambda completed: self._finish(job, completed))

    def _run_analysis(self, job: _Job) -> None:
        self._mark_running(job, "正在扫描项目并构建调用图")
        result = self._service_factory().analyze(job.project_path)
        with self._lock:
            job.graph = build_graph_document(result)

    def _run_annotation(self, job: _Job) -> None:
        self._mark_running(job, "正在生成函数、文件和项目三级注释")
        semantics = self._service_factory().annotate(job.project_path)
        with self._lock:
            job.semantics = semantics

    def _mark_running(self, job: _Job, message: str) -> None:
        with self._lock:
            job.state = "running"
            job.message = message
            job.updated_at = _utc_now()
            self._persist(job)

    def _finish(self, job: _Job, future: Future[None]) -> None:
        with self._lock:
            error = future.exception()
            job.updated_at = _utc_now()
            if error is None:
                job.state = "completed"
                job.message = "普通项目图已生成" if job.stage == "analyze" else "三级注释已生成"
                self._persist(job)
                return
            job.state = "failed"
            job.message = "任务执行失败"
            job.error = str(error)
            self._persist(job)

    def _get(self, job_id: str) -> _Job:
        job = self._jobs.get(job_id)
        if job is None:
            stored = self._store.get(job_id)
            if stored is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
            job = _Job.from_stored(stored)
            self._jobs[job_id] = job
        return job

    def _persist(self, job: _Job) -> None:
        """作用：把当前内存任务的最新状态和结果同步到 SQLite。"""

        self._store.save(job.stored())


def create_app(manager: MvpJobManager | None = None) -> FastAPI:
    api = FastAPI(title="MATLAB Refactor Agent Web API", version="0.1.0")
    api.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    jobs = manager or MvpJobManager()

    @api.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @api.post("/api/jobs/analyze", response_model=JobResponse, status_code=202)
    def analyze(request: AnalyzeRequest) -> JobResponse:
        return jobs.submit_analysis(request.project_path)

    @api.post("/api/jobs/{job_id}/annotate", response_model=JobResponse, status_code=202)
    def annotate(job_id: str) -> JobResponse:
        return jobs.submit_annotation(job_id)

    @api.get("/api/jobs/{job_id}", response_model=JobResponse)
    def job_status(job_id: str) -> JobResponse:
        return jobs.status(job_id)

    @api.get("/api/jobs/{job_id}/graph", response_model=GraphDocument)
    def graph(job_id: str) -> GraphDocument:
        return jobs.graph(job_id)

    @api.get("/api/jobs/{job_id}/graph/view", response_model=GraphViewDocument)
    def graph_view(
        job_id: str,
        scope: GraphScope = "project",
        focus_id: str | None = None,
        depth: Annotated[int, Query(ge=0, le=5)] = 1,
        limit: Annotated[int, Query(ge=1, le=150)] = 80,
        direction: GraphDirection = "both",
        query: str | None = None,
    ) -> GraphViewDocument:
        """作用：返回目录、文件或函数层级的有限可见调用图。"""

        return jobs.graph_view(
            job_id,
            scope=scope,
            focus_id=focus_id,
            depth=depth,
            limit=limit,
            direction=direction,
            query=query,
        )

    @api.get("/api/jobs/{job_id}/semantics", response_model=SemanticIndex)
    def semantics(job_id: str) -> SemanticIndex:
        return jobs.semantics(job_id)

    @api.get("/api/projects", response_model=list[JobResponse])
    def projects() -> list[JobResponse]:
        """作用：返回本机长期保存的全部 Web 项目。"""

        return jobs.projects()

    @api.delete("/api/projects/{job_id}", status_code=204)
    def delete_project(job_id: str) -> None:
        """作用：删除指定项目的本地图和三级语义快照。"""

        jobs.delete_project(job_id)

    return api


app = create_app()
