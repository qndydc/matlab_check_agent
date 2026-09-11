"""
Description: 管理本地迁移后台任务，并将真实断点和产物映射为前端视图。
References: MigrationService、ArtifactStore、MigrationCheckpointStore。
Referenced By: migration.backend.app 和迁移 Web 集成测试。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Callable, Literal
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, Field

from matlab_refactor_agent.application.migration_service import MigrationService
from matlab_refactor_agent.apps.semantic.backend.storage import (
    SQLiteWebProjectStore,
    StoredWebProject,
)
from matlab_refactor_agent.domain.diagnostics import DifferentialObservation
from matlab_refactor_agent.domain.exceptions import ArtifactError, OrchestrationError
from matlab_refactor_agent.domain.migration import (
    ActChunkPlan, ConversionStratagem, MatlabToPythonPlan,
    MigrationUnitState, TranslationResponse,
)
from matlab_refactor_agent.domain.models import AnalysisResult, ScanResult
from matlab_refactor_agent.domain.orchestration import new_job_id
from matlab_refactor_agent.domain.semantics import SemanticIndex
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore
from matlab_refactor_agent.infrastructure.config import AppSettings, load_settings
from matlab_refactor_agent.interfaces.api.multiuser import ControlStore, create_export
from matlab_refactor_agent.orchestration.execution_pool import (
    global_heavy_pool,
    global_job_coordinator,
)
from matlab_refactor_agent.migration.checkpoint import (
    CHECKPOINT_NAME, MigrationCheckpointStore, source_fingerprint,
    validate_resume_source,
)
from matlab_refactor_agent.orchestration.migration_state import MigrationStateStore
from matlab_refactor_agent.workers.scanning import validate_scan_inventory

RECORD_NAME = "migration-web.json"


class MigrationJob(BaseModel):
    """可持久化的 Web 请求；运行进度从已有 Agent 产物读取。"""

    job_id: str
    project_path: str
    employee_id: str = "local"
    project_id: str | None = None
    semantic_index_reference: str | None = None
    analysis_job_id: str | None = None
    scan_reference: str | None = None
    analysis_reference: str | None = None
    state: Literal["queued", "running", "completed", "failed", "manual_review", "interrupted"] = "queued"
    message: str = "等待迁移"
    error: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    owner: str = Field(default="", exclude=True)


def brief(message: object) -> str:
    """浏览器只显示一行错误，详细检查事实仍在各 WCC 面板中。"""
    return str(message).splitlines()[0][:240] if str(message).strip() else "未知错误"


class MigrationJobManager:
    """单用户、单后端进程；不再搭建第二套 Agent 或数据库。"""

    def __init__(self, settings: AppSettings | None = None,
                 service_factory: Callable[[], MigrationService] | None = None,
                 env_file: Path | None = None,
                 control_store: ControlStore | None = None) -> None:
        self.settings = settings or load_settings(env_file)
        self.artifacts = ArtifactStore(self.settings.orchestrator.artifact_dir)
        # 读取 5173 的项目静态分析快照；不能恢复/修改语义 Web Job 的运行状态。
        self._analysis_store = SQLiteWebProjectStore(
            self.settings.orchestrator.web_db, recover_interrupted=False
        )
        # 存储路径保持稳定；每个新任务/续跑重新读取模型和调试开关，
        # 运行中的客户端仍保持启动该任务时的配置。
        def runtime_settings() -> AppSettings:
            if settings is not None:
                return self.settings
            latest = load_settings(env_file)
            return self.settings.model_copy(
                update={"llm": latest.llm, "logging": latest.logging}
            )

        self._service_factory = service_factory or (
            lambda: MigrationService(runtime_settings())
        )
        self._executor = global_job_coordinator
        self._control = control_store or ControlStore(self.settings.orchestrator.web_db)
        self._lock = RLock()
        self._active: set[str] = set()
        self._owner = uuid4().hex

    def close(self) -> None:
        # The process-wide coordinator is shared with the Semantic application.
        return None

    def submit(
        self,
        project_path: str | None = None,
        semantic_reference: str | None = None,
        *,
        analysis_job_id: str | None = None,
        project_id: str | None = None,
        employee_id: str = "local",
    ) -> dict:
        """创建迁移 Job；Web 优先引用 5173 的项目静态分析快照。"""

        snapshot = self._analysis_snapshot(analysis_job_id, employee_id) if analysis_job_id else None
        if snapshot is not None:
            root, _, _ = self._validate_analysis_snapshot(snapshot)
            if project_path and Path(project_path.strip()).expanduser().resolve() != root:
                raise HTTPException(422, "填写的项目目录与所选项目静态分析不一致")
        elif project_id:
            uploaded = self._control.get_project(project_id, employee_id)
            if uploaded.state != "ready":
                raise HTTPException(409, "上传项目尚未处理完成")
            root = Path(uploaded.storage_path).resolve()
        else:
            if not project_path or not project_path.strip():
                raise HTTPException(422, "请选择项目静态分析，或填写 MATLAB 项目目录")
            if self._control.runtime.require_employee:
                raise HTTPException(422, "服务器模式只能选择已上传项目或已有分析")
            root = Path(project_path.strip()).expanduser().resolve()
            if not root.is_dir():
                raise HTTPException(422, "MATLAB 项目目录不存在（请填写后端所在机器的路径）")
        if self.artifacts.root.is_relative_to(root):
            raise HTTPException(422, "产物目录不能位于 MATLAB 输入项目内，请修改 ARTIFACT_DIR")

        manual_semantic_reference = semantic_reference.strip() if semantic_reference else ""
        semantic = snapshot.semantics if snapshot and not manual_semantic_reference else None
        if manual_semantic_reference:
            try:
                semantic = SemanticIndex.model_validate_json(
                    Path(manual_semantic_reference).expanduser().read_text(encoding="utf-8")
                )
                if Path(semantic.project_root).resolve() != root:
                    raise ValueError("语义索引所属项目与当前目录不一致")
            except (ValueError, OSError) as exc:
                raise HTTPException(422, f"语义索引无效：{brief(exc)}") from exc
        if semantic is not None and Path(semantic.project_root).expanduser().resolve() != root:
            raise HTTPException(422, "语义索引所属项目与当前目录不一致")

        job = MigrationJob(
            job_id=new_job_id(),
            project_path=str(root),
            employee_id=employee_id,
            project_id=project_id or (snapshot.project_id if snapshot else None),
            analysis_job_id=snapshot.job_id if snapshot else None,
            scan_reference=snapshot.scan_reference if snapshot else None,
            analysis_reference=snapshot.analysis_reference if snapshot else None,
        )
        if semantic is not None:
            job.semantic_index_reference = self.artifacts.write_model(
                job.job_id, "input-semantic-index.json", semantic
            )
        self._control.register_job(
            job.job_id, employee_id, "migration", project_id=job.project_id,
            state=job.state, stage="migration",
        )
        self._control.update_job(
            job.job_id,
            employee_id,
            settings_snapshot=json.dumps({
                "model": self.settings.llm.model,
                "base_url": self.settings.llm.base_url,
                "migration_max_agents": self.settings.llm.migration_max_agents,
                "migration_chunk_max_agents": self.settings.llm.migration_chunk_max_agents,
            }, ensure_ascii=False),
        )
        return self._submit(job, resume=False)

    def analysis_projects(self, employee_id: str = "local") -> list[dict]:
        """列出 5173 已完成的可选项目静态分析，并显示是否仍可安全复用。"""

        result: list[dict] = []
        for snapshot in self._analysis_store.analysis_snapshots(employee_id):
            item = {
                "analysis_job_id": snapshot.job_id,
                "project_path": str(snapshot.project_path),
                "semantics_ready": snapshot.semantics is not None,
                "updated_at": snapshot.updated_at.isoformat(),
                "message": snapshot.message,
                "usable": True,
                "unavailable_reason": None,
            }
            try:
                self._validate_analysis_snapshot(snapshot)
            except HTTPException as exc:
                item["usable"] = False
                item["unavailable_reason"] = str(exc.detail)
            result.append(item)
        return result

    def _analysis_snapshot(
        self, analysis_job_id: str, employee_id: str = "local"
    ) -> StoredWebProject:
        snapshot = self._analysis_store.get(analysis_job_id, employee_id)
        if snapshot is None or not all((
            snapshot.scan_reference,
            snapshot.analysis_reference,
            snapshot.source_fingerprint,
        )):
            raise HTTPException(
                404,
                "项目静态分析不存在或来自旧版本；请回到 5173 重新执行项目静态分析",
            )
        self._validate_analysis_snapshot(snapshot)
        return snapshot

    def _validate_analysis_snapshot(
        self, snapshot: StoredWebProject,
    ) -> tuple[Path, ScanResult, AnalysisResult]:
        """验证快照、artifact 根目录和当前源码，防止用过期调用图迁移。"""

        if not all((
            snapshot.scan_reference,
            snapshot.analysis_reference,
            snapshot.source_fingerprint,
        )):
            raise HTTPException(
                409,
                "项目静态分析缺少可复用快照；请回到 5173 重新执行项目静态分析",
            )
        root = snapshot.project_path.expanduser().resolve()
        if not root.is_dir():
            raise HTTPException(409, "MATLAB 项目目录已不存在，请重新选择项目静态分析")
        try:
            scan = self.artifacts.read_model(snapshot.scan_reference, ScanResult)
            analysis = self.artifacts.read_model(snapshot.analysis_reference, AnalysisResult)
            if Path(scan.project_root).expanduser().resolve() != root or (
                Path(analysis.project_root).expanduser().resolve() != root
            ):
                raise ValueError("快照所属目录不一致")
            if source_fingerprint(root, scan) != snapshot.source_fingerprint:
                raise ValueError("MATLAB 源码已变化")
        except (ArtifactError, OSError, OrchestrationError, ValueError) as exc:
            raise HTTPException(
                409,
                f"项目静态分析已不可复用：{brief(exc)}；请回到 5173 重新执行项目静态分析",
            ) from exc
        return root, scan, analysis

    def restart(self, job_id: str, employee_id: str = "local") -> dict:
        """New isolated job: rebuild analysis and graph, retain historical output."""
        with self._lock:
            job = self._get(job_id, employee_id)
            if job_id in self._active:
                raise HTTPException(409, "任务仍在运行")
            # Old analysis and semantic snapshots may be stale after source edits.
            return self.submit(
                job.project_path, project_id=job.project_id, employee_id=employee_id
            )

    def resume(self, job_id: str, employee_id: str = "local") -> dict:
        with self._lock:
            job = self._get(job_id, employee_id)
            if job_id in self._active:
                raise HTTPException(409, "任务仍在运行，请勿重复续跑")
            try:
                _, checkpoint = MigrationCheckpointStore(self.artifacts).load(job_id)
                if checkpoint.status == "completed":
                    raise HTTPException(409, "全部 WCC 已完成，无需续跑")
                scan = self.artifacts.read_model(checkpoint.scan_reference, ScanResult)
                validate_scan_inventory(scan, self.settings.project.exclude_patterns)
                validate_resume_source(checkpoint, Path(job.project_path), scan)
            except (ArtifactError, ValueError, OSError) as exc:
                raise HTTPException(409, "断点不可用，请新建迁移任务") from exc
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(409, brief(exc)) from exc
            return self._submit(job, resume=True)

    def _submit(self, job: MigrationJob, *, resume: bool) -> dict:
        with self._lock:
            job.state, job.error = "queued", None
            job.message = (
                "等待续跑未完成 WCC" if resume else
                "等待读取项目静态分析" if job.analysis_reference else
                "等待项目静态分析"
            )
            self._active.add(job.job_id)
            self._save(job)
            try:
                self._executor.submit(self._run, job, resume)
            except RuntimeError as exc:
                self._active.discard(job.job_id)
                job.state, job.error = "failed", "后端正在关闭，请重启后再试"
                self._save(job)
                raise HTTPException(503, job.error) from exc
            return self.status(job.job_id, job.employee_id)

    def _run(self, job: MigrationJob, resume: bool) -> None:
        try:
            with self._lock:
                job.state, job.message = "running", (
                    "恢复 WCC 断点" if resume else
                    "复用项目静态分析并规划 WCC" if job.analysis_reference else
                    "扫描项目并分析调用链"
                )
                self._save(job)
            service = self._service_factory()
            if resume:
                service.resume_translation_artifacts(job.job_id)
            else:
                service.generate_translation_artifacts(
                    Path(job.project_path), job_id=job.job_id,
                    semantic_index_reference=job.semantic_index_reference,
                    scan_reference=job.scan_reference,
                    analysis_reference=job.analysis_reference,
                )
            _, checkpoint = MigrationCheckpointStore(self.artifacts).load(job.job_id)
            job.state = checkpoint.status if checkpoint.status != "running" else "interrupted"
            job.message = {
                "completed": "项目级检查通过；人工复核建议已统一汇总",
                "manual_review": "项目级检查已执行，存在阻断问题；人工复核项已统一汇总",
                "interrupted": "任务未完成，请检查断点后续跑",
            }[job.state]
        except BaseException as exc:
            job.state, job.error = "failed", brief(exc)
            job.message = f"迁移失败：{job.error}"
        finally:
            with self._lock:
                self._save(job)
                self._active.discard(job.job_id)

    def _save(self, job: MigrationJob) -> None:
        # owner 仅用于区分服务重启，不作为前端字段公开。
        payload = {**job.model_dump(), "owner": self._owner}
        self.artifacts.write_text(job.job_id, RECORD_NAME, json.dumps(payload, ensure_ascii=False))
        self._control.update_job(
            job.job_id, job.employee_id, state=job.state,
            stage="migration", error=job.error, project_id=job.project_id,
        )

    def _get(self, job_id: str, employee_id: str | None = None) -> MigrationJob:
        try:
            job = self.artifacts.read_model(self.artifacts.reference(job_id, RECORD_NAME), MigrationJob)
        except ArtifactError as exc:
            raise HTTPException(404, "迁移任务不存在") from exc
        if employee_id is not None and job.employee_id != employee_id:
            raise HTTPException(404, "迁移任务不存在")
        if job.state in {"queued", "running"} and job.owner != self._owner:
            job.state = "interrupted"
            job.message = "上次服务已退出，可从未完成 WCC 续跑；无断点时请新建任务"
        return job

    def projects(self, employee_id: str = "local") -> list[dict]:
        result = []
        for path in sorted(self.artifacts.root.glob(f"*/{RECORD_NAME}"), reverse=True):
            try:
                result.append(self.status(path.parent.name, employee_id))
            except HTTPException:
                continue
        return result

    def export_job(self, job_id: str, employee_id: str = "local") -> Path:
        """Package generated Python files for the owning employee."""

        self._get(job_id, employee_id)
        target = self._control.export_path(employee_id, job_id)
        source = self.artifacts.root / job_id / "generated-python"
        return global_heavy_pool.run(create_export, source, target)

    def _states(self, job_id: str) -> list[MigrationUnitState]:
        path = self.artifacts.root / job_id / "migration-state.json"
        if not path.is_file():
            return []
        return MigrationStateStore(self.artifacts, job_id, str(path)).load()

    def events(self, job_id: str) -> list[dict]:
        self._get(job_id)
        path = self.artifacts.root / job_id / "migration-progress.json"
        return json.loads(self.artifacts.read_text(str(path))) if path.is_file() else []

    def _heartbeat(self, job_id: str) -> dict | None:
        path = self.artifacts.root / job_id / "migration-heartbeat.json"
        if not path.is_file():
            return None
        try:
            value = json.loads(self.artifacts.read_text(str(path)))
            return value if isinstance(value, dict) else None
        except (ArtifactError, json.JSONDecodeError, OSError):
            return None

    def status(self, job_id: str, employee_id: str | None = None) -> dict:
        with self._lock:
            job = self._get(job_id, employee_id)
            states = self._states(job_id)
            events = self.events(job_id)
            latest = events[-1] if events else None
            heartbeat = self._heartbeat(job_id)
            completed = sum(item.status == "frozen" for item in states)
            checkpoint_exists = (self.artifacts.root / job_id / CHECKPOINT_NAME).is_file()
            project_observation_path = (
                self.artifacts.root / job_id / "project-observation.json"
            )
            project_observation = (
                self.artifacts.read_model(
                    str(project_observation_path), DifferentialObservation
                ).model_dump()
                if project_observation_path.is_file() else None
            )
            active = latest["chain_id"] if latest and job.state != "completed" else None
            return {
                **job.model_dump(), "progress": completed / len(states) if states else 0,
                "active_chain_id": active, "semantic_index_used": bool(job.semantic_index_reference),
                "static_analysis_reused": bool(job.analysis_reference),
                "message": latest["message"] if latest and job.state == "running" else job.message,
                "completed_chains": completed, "total_chains": len(states),
                "heartbeat": heartbeat,
                "project_observation": project_observation,
                "can_resume": checkpoint_exists and job.state not in {"running", "queued", "completed"},
                "output_directory": str(self.artifacts.root / job_id / "generated-python"),
            }

    def _plan(self, job_id: str) -> MatlabToPythonPlan | None:
        self._get(job_id)
        if not (self.artifacts.root / job_id / CHECKPOINT_NAME).is_file():
            return None
        path = self.artifacts.root / job_id / "matlab-to-python-plan.json"
        return self.artifacts.read_model(str(path), MatlabToPythonPlan) if path.is_file() else None

    def _versions(self, job_id: str, prefix: str, chain_id: str) -> list[Path]:
        return sorted(
            (self.artifacts.root / job_id).glob(f"{prefix}-{chain_id}-*.json"),
            key=lambda path: int(path.stem.rsplit("-", 1)[-1]),
        )

    def _act_versions(self, job_id: str, chain_id: str) -> list[Path]:
        versions = self._versions(job_id, "act-attempt", chain_id)
        # 兼容 chunk 断点上线前的 WCC ActContext，并排除新 chunk 子文件。
        legacy = [
            path for path in self._versions(job_id, "act-context", chain_id)
            if path.stem.removeprefix(f"act-context-{chain_id}-").isdigit()
        ]
        by_number = {
            int(path.stem.rsplit("-", 1)[-1]): path
            for path in [*legacy, *versions]
        }
        return [by_number[number] for number in sorted(by_number)]

    def chains(self, job_id: str) -> list[dict]:
        plan = self._plan(job_id)
        states = {item.unit_id: item for item in self._states(job_id)}
        return [{
            "chain_id": unit.unit_id, "entry_symbols": unit.entry_symbols,
            "symbol_count": len(unit.symbol_ids), "scc_count": len(unit.sccs),
            "status": states.get(unit.unit_id, MigrationUnitState(unit_id=unit.unit_id)).status,
            "attempt_count": len(self._act_versions(job_id, unit.unit_id)),
        } for unit in plan.units] if plan else []

    def chain(self, job_id: str, chain_id: str) -> dict:
        plan = self._plan(job_id)
        unit = next((item for item in plan.units if item.unit_id == chain_id), None) if plan else None
        if unit is None:
            raise HTTPException(404, "WCC 不存在")
        summary = next(item for item in self.chains(job_id) if item["chain_id"] == chain_id)
        state = next((item for item in self._states(job_id) if item.unit_id == chain_id),
                     MigrationUnitState(unit_id=chain_id))
        _, checkpoint = MigrationCheckpointStore(self.artifacts).load(job_id)
        analysis = self.artifacts.read_model(checkpoint.analysis_reference, AnalysisResult)
        nodes = [{
            "id": f"scc-{index}", "label": ", ".join(members),
            "kind": "entry" if set(members) & set(unit.entry_symbols) else "scc",
            "status": state.status, "members": members,
        } for index, members in enumerate(unit.sccs)]
        owner = {symbol: node["id"] for node in nodes for symbol in node["members"]}
        edges = sorted({(owner[edge.source], owner[edge.target]) for edge in analysis.dependencies
                        if edge.source in owner and edge.target in owner
                        and owner[edge.source] != owner[edge.target]})
        strategies = self._versions(job_id, "stratagem", chain_id)
        stratagem = self.artifacts.read_model(str(strategies[-1]), ConversionStratagem) if strategies else None
        if stratagem and stratagem.action == "finish" and len(strategies) > 1:
            previous = self.artifacts.read_model(str(strategies[-2]), ConversionStratagem)
            stratagem = stratagem.model_copy(update={
                "conversion_steps": previous.conversion_steps,
                "matlab_semantic_risks": previous.matlab_semantic_risks,
                "validation_plan": previous.validation_plan,
            })
        observation = self.artifacts.read_model(state.observation_ref, DifferentialObservation) if state.observation_ref else None
        translation = self.artifacts.read_model(state.translation_ref, TranslationResponse) if state.translation_ref else None
        chunk_path = self.artifacts.root / job_id / f"act-chunks-{chain_id}.json"
        chunk_plan = (
            self.artifacts.read_model(str(chunk_path), ActChunkPlan)
            if chunk_path.is_file() else None
        )
        attempts = []
        for path in self._act_versions(job_id, chain_id):
            number = int(path.stem.rsplit("-", 1)[-1])
            checked = path.with_name(f"observation-{chain_id}-{number}.json")
            attempt_state = "generated"
            if checked.is_file():
                passed = self.artifacts.read_model(str(checked), DifferentialObservation).passed
                attempt_state = "accepted" if passed else "failed"
            attempts.append({"number": number, "state": attempt_state,
                             "summary": "检查通过" if attempt_state == "accepted" else
                             "检查发现问题" if attempt_state == "failed" else "已开始转换，尚无检查结果"})
        return {
            "summary": summary, "nodes": nodes,
            "edges": [{"source": source, "target": target} for source, target in edges],
            "stratagem": stratagem.model_dump() if stratagem else None,
            "observation": observation.model_dump() if observation else None,
            "attempts": attempts,
            "act_chunks": [{
                "chunk_id": item.chunk_id,
                "symbol_ids": item.symbol_ids,
                "depends_on_chunks": item.depends_on_chunks,
                "input_tokens": item.input_tokens,
                "output_tokens": item.output_tokens,
                "function_count": item.function_count,
                "module_count": item.module_count,
                "status": item.status,
                "attempts": item.attempts,
                "parent_chunk_id": item.parent_chunk_id,
                "last_error": item.last_error,
            } for item in chunk_plan.chunks] if chunk_plan else [],
            "files": [{"path": item.path, "language": "python", "content": item.content[:100_000]}
                      for item in translation.files] if translation else [],
            "semantic_summary": self._semantic_summary(checkpoint.semantic_index_reference, unit.symbol_ids),
        }

    def _semantic_summary(self, reference: str | None, symbols: list[str]) -> str | None:
        if not reference:
            return None
        index = self.artifacts.read_model(reference, SemanticIndex)
        parts = [f"项目：{index.project.purpose}"]
        parts.extend(f"文件 {item.file_path}：{item.role}" for item in index.files
                     if set(item.function_symbols) & set(symbols))
        parts.extend(f"函数 {item.symbol_id}：{item.summary}" for item in index.functions
                     if item.symbol_id in symbols)
        return "\n\n".join(parts)
