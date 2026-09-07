"""
Description: 用简单 LangGraph 串联 WCC 的 Reason、Act 与 Observation 闭环。
References: LangGraph、MigrationStateStore、domain.migration。
Referenced By: MatlabToPythonMigrationAgent 与迁移图测试。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from matlab_refactor_agent.domain.migration import ConversionStratagem, MatlabToPythonPlan
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore

from .migration_state import MigrationScheduler, MigrationStateStore


class MigrationGraphState(TypedDict, total=False):
    job_id: str
    plan_ref: str
    current_chain_id: str
    context_ref: str
    stratagem_ref: str
    translation_ref: str
    assembly_ref: str
    observation_ref: str
    migration_state_ref: str
    retry_count: int
    assigned_chain_id: str
    processed_assigned_chain: bool
    finalize_only: bool
    project_observation_ref: str


BuildContext = Callable[[str, str | None, str | None], str]
Reason = Callable[[str, str, str | None], str]
Act = Callable[[str, str, str], str]
Assemble = Callable[[str, str], str]
Observe = Callable[[str, str], str]
ProjectValidate = Callable[[str], str]


class MigrationGraph:
    """每次处理一个完整 WCC；SCC 原子边界由计划和 Act 提示共同保护。"""

    def __init__(self, artifacts: ArtifactStore, *, build_context: BuildContext,
                 reason: Reason, act: Act, assemble: Assemble, observe: Observe,
                 project_validate: ProjectValidate | None = None,
                 max_retries: int = 2,
                 on_step: Callable[[str, MigrationGraphState], None] | None = None) -> None:
        self.artifacts = artifacts
        self.build_context = build_context
        self.reason = reason
        self.act = act
        self.assemble = assemble
        self.observe = observe
        self.project_validate = project_validate
        self.max_retries = max_retries
        self.on_step = on_step
        self.graph = self._build().compile()

    def _store(self, state: MigrationGraphState) -> MigrationStateStore:
        return MigrationStateStore(
            self.artifacts, state["job_id"], state.get("migration_state_ref") or None
        )

    def _build(self) -> StateGraph:
        graph = StateGraph(MigrationGraphState)
        graph.add_node("initialize", self._with_progress("initialize", self._initialize))
        graph.add_node(
            "select_call_chain",
            self._with_progress("select_call_chain", self._select_call_chain),
        )
        for name in ("build_context", "reason", "act", "assemble", "observe",
                     "freeze_chain", "manual_review"):
            graph.add_node(name, self._with_progress(name, getattr(self, f"_{name}")))
        graph.add_node(
            "project_validate",
            self._with_progress("project_validate", self._project_validate),
        )
        graph.add_node("publish", self._with_progress("publish", lambda state: state))
        graph.add_edge(START, "initialize")
        graph.add_edge("initialize", "select_call_chain")
        graph.add_conditional_edges("select_call_chain", self._after_select, {
            "build_context": "build_context", "project_validate": "project_validate",
            "manual_review": "manual_review",
        })
        graph.add_edge("build_context", "reason")
        graph.add_conditional_edges("reason", self._after_reason, {
            "act": "act", "freeze_chain": "freeze_chain",
            "build_context": "build_context", "manual_review": "manual_review",
        })
        graph.add_edge("act", "assemble")
        graph.add_edge("assemble", "observe")
        graph.add_edge("observe", "reason")
        graph.add_edge("freeze_chain", "select_call_chain")
        graph.add_conditional_edges("project_validate", self._after_project_validate, {
            "publish": "publish", END: END,
        })
        graph.add_edge("publish", END)
        graph.add_conditional_edges("manual_review",
            lambda state: "select_call_chain",
            {"select_call_chain": "select_call_chain"})
        return graph

    def _with_progress(self, name: str, operation: Callable):
        def run(state: MigrationGraphState):
            if self.on_step is not None:
                self.on_step(name, state)
            return operation(state)
        return run

    def _initialize(self, state: MigrationGraphState) -> dict[str, object]:
        if state.get("migration_state_ref"):
            return {}
        plan = self.artifacts.read_model(state["plan_ref"], MatlabToPythonPlan)
        reference = MigrationStateStore(self.artifacts, state["job_id"]).initialize(plan)
        return {"migration_state_ref": reference, "retry_count": 0}

    def _select_call_chain(self, state: MigrationGraphState) -> dict[str, object]:
        assigned = state.get("assigned_chain_id", "")
        if assigned and not state.get("processed_assigned_chain", False):
            return {
                "current_chain_id": assigned, "context_ref": "", "stratagem_ref": "",
                "translation_ref": "", "assembly_ref": "", "observation_ref": "",
                "retry_count": 0, "processed_assigned_chain": True,
            }
        if assigned:
            return {"current_chain_id": ""}
        ready = MigrationScheduler.ready(self._store(state).load())
        if not ready:
            return {"current_chain_id": ""}
        return {
            "current_chain_id": ready[0].unit_id, "context_ref": "",
            "stratagem_ref": "", "translation_ref": "", "assembly_ref": "",
            "observation_ref": "", "retry_count": 0,
        }

    def _after_select(self, state: MigrationGraphState) -> Literal[
        "build_context", "project_validate", "__end__"
    ]:
        if state.get("current_chain_id"):
            return "build_context"
        if state.get("assigned_chain_id") and not state.get("finalize_only"):
            return END
        return "project_validate"

    def _project_validate(self, state: MigrationGraphState) -> dict[str, object]:
        if self.project_validate is None:
            return {}
        reference = self.project_validate(state["migration_state_ref"])
        return {"project_observation_ref": reference}

    def _after_project_validate(
        self, state: MigrationGraphState
    ) -> Literal["publish", "__end__"]:
        states = self._store(state).load()
        if not states or not all(item.status == "frozen" for item in states):
            return END
        if self.project_validate is None:
            return "publish"
        from matlab_refactor_agent.domain.diagnostics import DifferentialObservation
        reference = state.get("project_observation_ref")
        return "publish" if reference and self.artifacts.read_model(
            reference, DifferentialObservation
        ).passed else END

    def _build_context(self, state: MigrationGraphState) -> dict[str, object]:
        retry_count = state.get("retry_count", 0)
        if state.get("stratagem_ref"):
            previous = self.artifacts.read_model(
                state["stratagem_ref"], ConversionStratagem
            )
            if previous.action == "rebuild_context":
                retry_count += 1
        return {"context_ref": self.build_context(
            state["current_chain_id"], state.get("observation_ref") or None,
            state.get("stratagem_ref") or None,
        ), "retry_count": retry_count}

    def _reason(self, state: MigrationGraphState) -> dict[str, object]:
        return {"stratagem_ref": self.reason(
            state["current_chain_id"], state["context_ref"],
            state.get("observation_ref") or None,
        )}

    def _after_reason(self, state: MigrationGraphState) -> Literal[
        "act", "freeze_chain", "build_context", "manual_review"
    ]:
        stratagem = self.artifacts.read_model(
            state["stratagem_ref"], ConversionStratagem
        )
        if stratagem.action == "finish":
            from matlab_refactor_agent.domain.diagnostics import DifferentialObservation
            reference = state.get("observation_ref")
            verified = reference and self.artifacts.read_model(
                reference, DifferentialObservation
            ).passed
            return "freeze_chain" if (
                verified and state.get("translation_ref") and state.get("assembly_ref")
            ) else "manual_review"
        if stratagem.action == "rebuild_context":
            return "manual_review" if state.get("retry_count", 0) >= self.max_retries else "build_context"
        if stratagem.action in {"replan", "manual_review"}:
            return "manual_review"
        return "manual_review" if state.get("retry_count", 0) >= self.max_retries else "act"

    def _act(self, state: MigrationGraphState) -> dict[str, object]:
        reference = self.act(
            state["current_chain_id"], state["context_ref"], state["stratagem_ref"]
        )
        store = self._store(state)
        store.update(state["current_chain_id"], status="generated", translation_ref=reference)
        return {"translation_ref": reference, "migration_state_ref": store.reference}

    def _assemble(self, state: MigrationGraphState) -> dict[str, object]:
        reference = self.assemble(state["current_chain_id"], state["translation_ref"])
        store = self._store(state)
        store.update(state["current_chain_id"], assembly_ref=reference)
        return {"assembly_ref": reference, "migration_state_ref": store.reference}

    def _observe(self, state: MigrationGraphState) -> dict[str, object]:
        reference = self.observe(state["current_chain_id"], state["assembly_ref"])
        from matlab_refactor_agent.domain.diagnostics import DifferentialObservation
        observation = self.artifacts.read_model(reference, DifferentialObservation)
        store = self._store(state)
        retry_count = state.get("retry_count", 0) + 1
        store.update(state["current_chain_id"],
                     status="verified" if observation.passed else "failed",
                     observation_ref=reference, retry_count=retry_count)
        return {"observation_ref": reference, "migration_state_ref": store.reference,
                "retry_count": retry_count}

    def _freeze_chain(self, state: MigrationGraphState) -> dict[str, object]:
        store = self._store(state)
        store.update(state["current_chain_id"], status="frozen")
        return {"migration_state_ref": store.reference}

    def _manual_review(self, state: MigrationGraphState) -> dict[str, object]:
        chain_id = state.get("current_chain_id")
        if not chain_id:
            return {}
        store = self._store(state)
        store.update(chain_id, status="manual_review")
        return {"migration_state_ref": store.reference}
