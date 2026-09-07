"""
Description: 作为语义流水线内部流图，编排函数簇注释、自检反馈、有限重试和三级聚合。
References: LangGraph、ClusterSemanticAnnotator、SemanticAggregator、ArtifactStore。
Referenced By: MainWorkflow、语义图单元测试和未来 checkpoint 入口。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, Protocol, TypedDict
from pathlib import Path

from langgraph.graph import END, START, StateGraph

from matlab_refactor_agent.domain.models import AnalysisResult
from matlab_refactor_agent.domain.semantics import (
    ClusterAnnotationResponse,
    SemanticConflicts,
    SemanticIndex,
    SemanticPreparationBundle,
    SemanticAnnotationRequest,
    SemanticProgressEvent,
    SemanticProgressLog,
    SemanticQualityReport,
    SemanticUnitQuality,
    SemanticWorkUnit,
    SemanticWorkUnits,
)
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore
from matlab_refactor_agent.domain.exceptions import CallLifecycleError
from matlab_refactor_agent.semantics.aggregator import SemanticAggregator
from matlab_refactor_agent.semantics.quality import SemanticQualityReviewer
from matlab_refactor_agent.semantics.checkpoint import SemanticCheckpoint


class SemanticGraphState(TypedDict, total=False):
    """LangGraph 轻量状态；源码、注释和索引只通过 artifact 引用传递。"""

    preparation_ref: str
    pending_unit_ids: list[str]
    current_unit_id: str
    attempts: dict[str, int]
    annotation_refs: dict[str, str]
    feedback: dict[str, list[str]]
    quality_history: list[dict[str, object]]
    manual_review_unit_ids: list[str]
    annotation_error: str
    semantic_index_ref: str
    conflicts_ref: str
    quality_report_ref: str
    progress_log_ref: str


SemanticProgressHandler = Callable[[SemanticProgressEvent], None]


class ClusterAnnotator(Protocol):
    """语义流图只依赖强类型注释器接口，不依赖通用 Agent 运行时。"""

    def annotate(self, request: SemanticAnnotationRequest) -> str: ...


class SemanticAnnotationGraph:
    """对低置信度或未完成函数簇执行有限重试，随后聚合三级语义。"""

    def __init__(
        self,
        artifacts: ArtifactStore,
        annotator: ClusterAnnotator,
        aggregator: SemanticAggregator,
        *,
        confidence_threshold: float = 0.6,
        max_attempts: int = 2,
        quality_reviewer: SemanticQualityReviewer | None = None,
        debug_model: bool = False,
    ) -> None:
        if not 0 <= confidence_threshold <= 1:
            raise ValueError("confidence_threshold 必须位于 0 到 1")
        if max_attempts < 1:
            raise ValueError("max_attempts 必须至少为 1")
        self._artifacts = artifacts
        self._annotator = annotator
        self._aggregator = aggregator
        self._threshold = confidence_threshold
        self._max_attempts = max_attempts
        self._quality_reviewer = quality_reviewer or SemanticQualityReviewer(
            confidence_threshold
        )
        self._debug_model = debug_model
        self._event_handler: SemanticProgressHandler | None = None
        self._events: list[SemanticProgressEvent] = []
        self._sequence = 0
        self._active_node = "initialize"
        self.graph = self._build().compile()

    def invoke(
        self,
        preparation_ref: str,
        event_handler: SemanticProgressHandler | None = None,
        *,
        resume: bool = False,
    ) -> SemanticGraphState:
        """运行完整语义循环并返回只含 artifact 引用的最终状态。"""

        self._event_handler = event_handler
        self._events = []
        self._sequence = 0
        bundle = self._artifacts.read_model(
            preparation_ref, SemanticPreparationBundle
        )
        self._resume = resume
        self._attempt_baseline: dict[str, int] = {}
        progress_path = self._artifacts.root / bundle.job_id / "semantic-progress.json"
        if resume and progress_path.is_file():
            self._events = self._artifacts.read_model(str(progress_path), SemanticProgressLog).events
            self._sequence = max((event.sequence for event in self._events), default=0)
        unit_count = len(self._artifacts.read_model(bundle.work_units_reference, SemanticWorkUnits).units)
        try:
            result = self.graph.invoke({"preparation_ref": preparation_ref},
                                       {"recursion_limit": max(25, unit_count * self._max_attempts * 3 + 10)})
        except Exception as exc:
            self._emit(
                job_id=bundle.job_id,
                node=self._active_node,  # type: ignore[arg-type]
                phase="failed",
                message=f"语义图在 {self._active_node} 节点失败：{exc}",
                details={"error": str(exc)},
            )
            self._write_progress_log(bundle.job_id)
            raise
        progress_reference = self._write_progress_log(bundle.job_id)
        result["progress_log_ref"] = progress_reference
        return result

    def _write_progress_log(self, job_id: str) -> str:
        return self._artifacts.write_model(
            job_id,
            "semantic-progress.json",
            SemanticProgressLog(job_id=job_id, events=self._events),
        )

    def _emit(
        self,
        *,
        job_id: str,
        node: Literal[
            "initialize", "select_unit", "annotate", "quality_check", "aggregate"
        ],
        phase: Literal[
            "started", "completed", "retrying", "manual_review", "failed"
        ],
        message: str,
        unit_id: str | None = None,
        attempt: int | None = None,
        quality_status: Literal[
            "accepted", "low_quality", "incomplete", "manual_review"
        ] | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        """同步发布有序事件；监控回调失败不影响语义任务本身。"""

        self._sequence += 1
        self._active_node = node
        event = SemanticProgressEvent(
            job_id=job_id,
            sequence=self._sequence,
            node=node,
            phase=phase,
            message=message,
            unit_id=unit_id,
            attempt=attempt,
            quality_status=quality_status,
            details=details or {},
        )
        DEBUG_MODEL = self._debug_model
        if DEBUG_MODEL:
            print(
                f"[DEBUG_MODEL][SEMANTIC][{node.upper()}][{phase.upper()}] "
                f"job={job_id} unit={unit_id or '-'} attempt={attempt or '-'} "
                f"message={message}",
                flush=True,
            )
        self._events.append(event)
        self._write_progress_log(job_id)
        if self._event_handler is not None:
            try:
                self._event_handler(event)
            except Exception:
                pass

    def _build(self) -> StateGraph:
        builder = StateGraph(SemanticGraphState)
        builder.add_node("initialize", self._initialize)
        builder.add_node("select_unit", self._select_unit)
        builder.add_node("annotate", self._annotate)
        builder.add_node("quality_check", self._quality_check)
        builder.add_node("aggregate", self._aggregate)
        builder.add_edge(START, "initialize")
        builder.add_edge("initialize", "select_unit")
        builder.add_conditional_edges(
            "select_unit",
            self._after_select,
            {"annotate": "annotate", "aggregate": "aggregate"},
        )
        builder.add_edge("annotate", "quality_check")
        builder.add_edge("quality_check", "select_unit")
        builder.add_edge("aggregate", END)
        return builder

    def _bundle(self, state: SemanticGraphState) -> SemanticPreparationBundle:
        return self._artifacts.read_model(
            state["preparation_ref"], SemanticPreparationBundle
        )

    def _units(self, state: SemanticGraphState) -> dict[str, SemanticWorkUnit]:
        bundle = self._bundle(state)
        document = self._artifacts.read_model(
            bundle.work_units_reference, SemanticWorkUnits
        )
        return {unit.unit_id: unit for unit in document.units}

    def _initialize(self, state: SemanticGraphState) -> dict[str, object]:
        bundle = self._bundle(state)
        self._emit(
            job_id=bundle.job_id,
            node="initialize",
            phase="started",
            message="正在读取语义准备清单",
        )
        units = self._units(state)
        self._emit(
            job_id=bundle.job_id,
            node="initialize",
            phase="completed",
            message=f"语义图已初始化，共 {len(units)} 个函数簇",
            details={"unit_count": len(units)},
        )
        initial = {
            "pending_unit_ids": list(units),
            "current_unit_id": "",
            "attempts": {},
            "annotation_refs": {},
            "feedback": {},
            "quality_history": [],
            "manual_review_unit_ids": [],
            "annotation_error": "",
        }
        if self._resume:
            checkpoint = self._artifacts.read_model(
                self._artifacts.reference(bundle.job_id, "semantic-checkpoint.json"), SemanticCheckpoint
            )
            initial.update(checkpoint.graph_state)
            latest = {item["unit_id"]: item for item in initial["quality_history"]}
            accepted = set()
            for unit_id, item in latest.items():
                reference = initial["annotation_refs"].get(unit_id)
                if item["status"] == "accepted" and reference and Path(reference).is_file():
                    response = self._artifacts.read_model(reference, ClusterAnnotationResponse)
                    if self._quality_reviewer.review(units[unit_id], response).status == "accepted":
                        accepted.add(unit_id)
            initial["pending_unit_ids"] = [unit_id for unit_id in units if unit_id not in accepted]
            initial["manual_review_unit_ids"] = []
            self._attempt_baseline = dict(initial["attempts"])
        return initial

    def _save_checkpoint(self, state: SemanticGraphState, **updates: object) -> None:
        bundle = self._bundle(state)
        path = self._artifacts.root / bundle.job_id / "semantic-checkpoint.json"
        # Standalone graph callers need not create a resumable pipeline session.
        if not path.is_file():
            return
        checkpoint = self._artifacts.read_model(str(path), SemanticCheckpoint)
        checkpoint.graph_state = {**state, **updates}
        self._artifacts.write_model(bundle.job_id, path.name, checkpoint)

    def _select_unit(self, state: SemanticGraphState) -> dict[str, object]:
        bundle = self._bundle(state)
        pending = list(state.get("pending_unit_ids", []))
        current = pending.pop(0) if pending else ""
        self._emit(
            job_id=bundle.job_id,
            node="select_unit",
            phase="completed",
            message=(
                f"选择函数簇 {current}，队列剩余 {len(pending)} 个"
                if current
                else "全部函数簇已处理，准备聚合三级语义"
            ),
            unit_id=current or None,
            details={"remaining_units": len(pending)},
        )
        return {"pending_unit_ids": pending, "current_unit_id": current}

    @staticmethod
    def _after_select(
        state: SemanticGraphState,
    ) -> Literal["annotate", "aggregate"]:
        return "annotate" if state.get("current_unit_id") else "aggregate"

    def _annotate(self, state: SemanticGraphState) -> dict[str, object]:
        bundle = self._bundle(state)
        unit_id = state["current_unit_id"]
        unit = self._units(state)[unit_id]
        attempts = dict(state.get("attempts", {}))
        attempt = attempts.get(unit_id, 0) + 1
        attempts[unit_id] = attempt
        self._save_checkpoint(state, attempts=attempts)
        self._emit(
            job_id=bundle.job_id,
            node="annotate",
            phase="started",
            message=f"正在注释函数簇 {unit_id}（第 {attempt} 次）",
            unit_id=unit_id,
            attempt=attempt,
        )
        if self._debug_model:
            self._emit(
                job_id=bundle.job_id,
                node="annotate",
                phase="started",
                message=(
                    f"准备函数簇模型上下文：{len(unit.symbol_ids)} 个函数，"
                    f"估算 {unit.estimated_tokens} tokens"
                ),
                unit_id=unit_id,
                attempt=attempt,
                details={
                    "symbol_ids": unit.symbol_ids,
                    "estimated_tokens": unit.estimated_tokens,
                },
            )
        annotation_refs = dict(state.get("annotation_refs", {}))
        try:
            annotation_refs[unit_id] = self._annotator.annotate(
                SemanticAnnotationRequest(
                    job_id=bundle.job_id,
                    scan_reference=bundle.scan_reference,
                    analysis_reference=bundle.analysis_reference,
                    unit=unit,
                    token_budget=bundle.token_budget,
                    hard_token_limit=bundle.hard_token_limit,
                    attempt=attempt,
                    quality_feedback=state.get("feedback", {}).get(
                        unit_id, []
                    ),
                    previous_annotation_reference=state.get(
                        "annotation_refs", {}
                    ).get(unit_id),
                )
            )
            error = ""
            self._emit(
                job_id=bundle.job_id,
                node="annotate",
                phase="completed",
                message=f"函数簇 {unit_id} 已生成注释",
                unit_id=unit_id,
                attempt=attempt,
            )
        except CallLifecycleError as exc:
            error = str(exc)
            self._emit(
                job_id=bundle.job_id,
                node="annotate",
                phase="failed",
                message=f"函数簇 {unit_id} 调用失败：{error}",
                unit_id=unit_id,
                attempt=attempt,
                details={
                    "error": error,
                    "error_type": exc.error_type,
                    "retryable": exc.retryable,
                },
            )
            # 网络与配置故障由调用层处理；耗尽后中断并保留断点，
            # 不把它错误地计入语义质量重试。
            if exc.error_type not in {"invalid_response", "output_truncated"}:
                attempts[unit_id] = max(0, attempts[unit_id] - 1)
                self._save_checkpoint(state, attempts=attempts)
                raise
        except Exception as exc:  # 图必须把普通注释失败转为可路由的观察。
            error = str(exc)
            self._emit(
                job_id=bundle.job_id,
                node="annotate",
                phase="failed",
                message=f"函数簇 {unit_id} 注释失败：{error}",
                unit_id=unit_id,
                attempt=attempt,
                details={"error": error},
            )
        return {
            "attempts": attempts,
            "annotation_refs": annotation_refs,
            "annotation_error": error,
        }

    def _quality_check(self, state: SemanticGraphState) -> dict[str, object]:
        bundle = self._bundle(state)
        unit_id = state["current_unit_id"]
        unit = self._units(state)[unit_id]
        attempt = state["attempts"][unit_id]
        reference = state.get("annotation_refs", {}).get(unit_id)
        response = (
            self._artifacts.read_model(reference, ClusterAnnotationResponse)
            if reference
            else None
        )
        decision = self._quality_reviewer.review(
            unit,
            response,
            annotation_error=state.get("annotation_error", ""),
        )
        if self._debug_model:
            self._emit(
                job_id=bundle.job_id,
                node="quality_check",
                phase="completed",
                message=(
                    f"质量检查得到 {decision.status}，"
                    f"共 {len(decision.reasons)} 条反馈"
                ),
                unit_id=unit_id,
                attempt=attempt,
                quality_status=decision.status,
                details={"reasons": decision.reasons},
            )
        status: Literal[
            "accepted", "low_quality", "incomplete", "manual_review"
        ] = decision.status
        reasons = decision.reasons

        pending = list(state.get("pending_unit_ids", []))
        feedback = {key: list(value) for key, value in state.get("feedback", {}).items()}
        manual = list(state.get("manual_review_unit_ids", []))
        if status != "accepted":
            feedback[unit_id] = reasons
            if attempt - self._attempt_baseline.get(unit_id, 0) < self._max_attempts:
                pending.append(unit_id)
            else:
                status = "manual_review"
                if unit_id not in manual:
                    manual.append(unit_id)
        if status == "accepted":
            phase = "completed"
            message = f"函数簇 {unit_id} 通过语义质量检查"
        elif status == "manual_review":
            phase = "manual_review"
            message = f"函数簇 {unit_id} 达到重试上限，转人工复核"
        else:
            phase = "retrying"
            message = f"函数簇 {unit_id} 质量不足，已重新加入队列"
        self._emit(
            job_id=bundle.job_id,
            node="quality_check",
            phase=phase,
            message=message,
            unit_id=unit_id,
            attempt=attempt,
            quality_status=status,
            details={"reasons": reasons},
        )
        quality = SemanticUnitQuality(
            unit_id=unit_id,
            attempt=attempt,
            status=status,
            reasons=reasons,
            annotation_reference=reference,
        )
        history = [*state.get("quality_history", []), quality.model_dump(mode="json")]
        updates = {
            "pending_unit_ids": pending,
            "feedback": feedback,
            "manual_review_unit_ids": manual,
            "quality_history": history,
            "annotation_error": "",
        }
        self._save_checkpoint(state, **updates)
        return updates

    def _aggregate(self, state: SemanticGraphState) -> dict[str, object]:
        bundle = self._bundle(state)
        self._emit(
            job_id=bundle.job_id,
            node="aggregate",
            phase="started",
            message="正在聚合函数、文件和项目三级语义",
        )
        analysis = self._artifacts.read_model(
            bundle.analysis_reference, AnalysisResult
        )
        responses = [
            self._artifacts.read_model(reference, ClusterAnnotationResponse)
            for _, reference in sorted(state.get("annotation_refs", {}).items())
        ]
        if self._debug_model:
            self._emit(
                job_id=bundle.job_id,
                node="aggregate",
                phase="started",
                message=f"已加载 {len(responses)} 个函数簇结果，开始文件级与项目级聚合",
                details={"cluster_responses": len(responses)},
            )
        index = self._aggregator.aggregate(analysis, responses)
        index_reference = self._artifacts.write_model(
            bundle.job_id, "semantic-index.json", index
        )
        conflicts_reference = self._artifacts.write_model(
            bundle.job_id,
            "semantic-conflicts.json",
            SemanticConflicts(conflicts=index.conflicts),
        )
        history = [SemanticUnitQuality.model_validate(item) for item in state.get("quality_history", [])]
        latest = {item.unit_id: item for item in history}
        report = SemanticQualityReport(
            units=history,
            accepted_unit_ids=sorted(
                unit_id for unit_id, item in latest.items() if item.status == "accepted"
            ),
            low_quality_unit_ids=sorted(
                {item.unit_id for item in history if item.status == "low_quality"}
            ),
            incomplete_unit_ids=sorted(
                {item.unit_id for item in history if item.status == "incomplete"}
            ),
            manual_review_unit_ids=sorted(state.get("manual_review_unit_ids", [])),
        )
        report_reference = self._artifacts.write_model(
            bundle.job_id, "semantic-quality-report.json", report
        )
        self._emit(
            job_id=bundle.job_id,
            node="aggregate",
            phase="completed",
            message=(
                f"三级语义聚合完成，接受 {len(report.accepted_unit_ids)} 个簇，"
                f"人工复核 {len(report.manual_review_unit_ids)} 个簇"
            ),
            details={
                "accepted_units": len(report.accepted_unit_ids),
                "manual_review_units": len(report.manual_review_unit_ids),
            },
        )
        return {
            "semantic_index_ref": index_reference,
            "conflicts_ref": conflicts_reference,
            "quality_report_ref": report_reference,
        }


__all__ = [
    "SemanticAnnotationGraph",
    "SemanticGraphState",
    "SemanticProgressHandler",
    "ClusterAnnotator",
]
