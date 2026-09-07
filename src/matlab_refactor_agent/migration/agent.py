"""
Description: 组合迁移会话、WCC 运行组件和 LangGraph 反馈循环。
References: MigrationSessionManager、MigrationRuntime、MigrationGraph。
Referenced By: MainWorkflow 和 MigrationService。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore
from matlab_refactor_agent.infrastructure.llm import StructuredLLMClient
from matlab_refactor_agent.orchestration.migration_graph import MigrationGraph
from matlab_refactor_agent.orchestration.migration_state import (
    MigrationScheduler,
    MigrationStateStore,
)

from .checkpoint import MigrationSession, MigrationSessionManager
from .runtime import MigrationRuntime


@dataclass(frozen=True)
class MigrationAgentResult:
    """迁移 Agent Loop 的计划、状态和生成产物引用。"""

    plan_ref: str
    migration_state_ref: str
    translation_refs: list[str] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)


class MatlabToPythonMigrationAgent:
    """只负责编排会话、运行组件和 Reason/Act/Observation 图。"""

    def __init__(
        self,
        *,
        artifacts: ArtifactStore,
        client: StructuredLLMClient,
        reason_client: StructuredLLMClient | None = None,
        max_retries: int = 2,
        reason_input_budget: int = 32_768,
        debug_model: bool = False,
        max_concurrency: int = 1,
        chunk_concurrency: int = 1,
    ) -> None:
        self._artifacts = artifacts
        self._client = client
        self._reason_client = reason_client or client
        self._max_retries = max_retries
        self._reason_input_budget = reason_input_budget
        self._debug_model = debug_model
        self._max_concurrency = max(1, max_concurrency)
        self._chunk_concurrency = max(1, chunk_concurrency)

    def run(
        self,
        *,
        job_id: str,
        scan_reference: str,
        analysis_reference: str,
        semantic_index_reference: str | None = None,
        plan_reference: str | None = None,
        migration_state_reference: str | None = None,
        resume: bool = False,
    ) -> MigrationAgentResult:
        """运行或恢复完整迁移闭环。"""

        DEBUG_MODEL = self._debug_model
        if DEBUG_MODEL:
            print(
                f"[DEBUG_MODEL][MIGRATION][AGENT][START] job={job_id} "
                f"resume={resume} max_retries={self._max_retries}",
                flush=True,
            )

        sessions = MigrationSessionManager(self._artifacts)
        session = sessions.open(
            job_id=job_id,
            scan_reference=scan_reference,
            analysis_reference=analysis_reference,
            semantic_index_reference=semantic_index_reference,
            plan_reference=plan_reference,
            state_reference=migration_state_reference,
            resume=resume,
        )
        try:
            graph_state, runtime_artifacts = self._run_graph(session)
        except BaseException:
            sessions.interrupt(session)
            if DEBUG_MODEL:
                print(
                    f"[DEBUG_MODEL][MIGRATION][AGENT][INTERRUPTED] "
                    f"job={job_id}",
                    flush=True,
                )
            raise
        completed = sessions.complete(
            session,
            str(graph_state["migration_state_ref"]),
            self._with_call_log(job_id, runtime_artifacts),
        )
        if DEBUG_MODEL:
            print(
                f"[DEBUG_MODEL][MIGRATION][AGENT][DONE] job={job_id} "
                f"planned_wcc={len(session.plan.units)} "
                f"translations={len(completed.translation_references)}",
                flush=True,
            )
        return MigrationAgentResult(
            plan_ref=completed.plan_reference,
            migration_state_ref=completed.state_reference,
            translation_refs=completed.translation_references,
            artifacts=completed.artifacts,
        )

    def _runtime(self, session: MigrationSession) -> MigrationRuntime:
        return MigrationRuntime(
            session=session, artifacts=self._artifacts, client=self._client,
            reason_client=self._reason_client,
            reason_input_budget=self._reason_input_budget,
            debug_model=self._debug_model,
            chunk_concurrency=self._chunk_concurrency,
        )

    def _run_graph(
        self, session: MigrationSession
    ) -> tuple[dict[str, object], dict[str, str]]:
        if self._max_concurrency == 1:
            runtime = self._runtime(session)
            try:
                state = self._graph(runtime).invoke(
                    session.graph_input(),
                    {"recursion_limit": 20 + len(session.plan.units) *
                        (12 + 8 * self._max_retries)},
                )
            except BaseException:
                if runtime.current_chain_id:
                    MigrationStateStore(
                        self._artifacts, session.job_id,
                        session.checkpoint.migration_state_reference,
                    ).update(runtime.current_chain_id, status="failed")
                raise
            return state, runtime.artifacts
        state_store = MigrationStateStore(
            self._artifacts, session.job_id,
            session.checkpoint.migration_state_reference,
        )
        last_state: dict[str, object] = session.graph_input()
        artifacts: dict[str, str] = {}
        while ready := MigrationScheduler.ready(state_store.load()):
            wave = ready[:self._max_concurrency]
            with ThreadPoolExecutor(
                max_workers=len(wave), thread_name_prefix="migration-wcc"
            ) as executor:
                results = list(executor.map(
                    lambda unit: self._run_assigned(session, unit.unit_id), wave
                ))
            for state, produced in results:
                last_state = state
                artifacts.update(produced)
        runtime = self._runtime(session)
        last_state = self._graph(runtime).invoke({
            **session.graph_input(),
            "migration_state_ref": state_store.reference,
            "finalize_only": True,
        })
        artifacts.update(runtime.artifacts)
        return last_state, artifacts

    def _run_assigned(
        self, session: MigrationSession, unit_id: str
    ) -> tuple[dict[str, object], dict[str, str]]:
        runtime = self._runtime(session)
        graph_input = {
            **session.graph_input(),
            "assigned_chain_id": unit_id,
            "processed_assigned_chain": False,
        }
        try:
            state = self._graph(runtime).invoke(
                graph_input,
                {"recursion_limit": 20 + 8 * self._max_retries},
            )
        except BaseException:
            MigrationStateStore(
                self._artifacts, session.job_id,
                session.checkpoint.migration_state_reference,
            ).update(unit_id, status="failed")
            raise
        return state, runtime.artifacts

    def _with_call_log(self, job_id: str, artifacts: dict[str, str]) -> dict[str, str]:
        path = self._artifacts.root / job_id / "call-observations.json"
        if path.is_file():
            artifacts["call_observations"] = str(path)
        model_calls = self._artifacts.root / job_id / "migration-model-calls.json"
        if model_calls.is_file():
            artifacts["migration_model_calls"] = str(model_calls)
        return artifacts

    def _graph(self, runtime: MigrationRuntime):
        """连接五个明确组件；节点路由仍由 MigrationGraph 管理。"""

        return MigrationGraph(
            self._artifacts,
            build_context=runtime.build_context,
            reason=runtime.reason,
            act=runtime.act,
            assemble=runtime.assemble,
            observe=runtime.observe,
            project_validate=runtime.project_validate,
            on_step=runtime.record_step,
            max_retries=self._max_retries,
        ).graph


__all__ = ["MatlabToPythonMigrationAgent", "MigrationAgentResult"]
