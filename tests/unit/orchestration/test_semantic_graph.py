"""
Description: 验证语义 Pipeline 会将低质量簇和未完成簇反馈后重新排队。
References: SemanticAnnotationGraph、ArtifactStore、FakeStructuredLLMClient。
Referenced By: pytest 语义 Pipeline 回归套件。
"""

from pathlib import Path
from threading import Barrier, Lock

from matlab_refactor_agent.domain.models import AnalysisResult, FunctionInfo
from matlab_refactor_agent.domain.semantics import (
    ClusterAnnotationResponse,
    FileAnnotationDraft,
    FunctionAnnotation,
    ProjectAnnotationDraft,
    SemanticPreparationBundle,
    SemanticAnnotationRequest,
    SemanticProgressEvent,
    SemanticQualityReport,
    SemanticWorkUnit,
    SemanticWorkUnits,
    SourceEvidence,
)
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore
from matlab_refactor_agent.infrastructure.llm import FakeStructuredLLMClient
from matlab_refactor_agent.orchestration.semantic_graph import SemanticAnnotationGraph
from matlab_refactor_agent.semantics import SemanticAggregator


class _SequenceAnnotator:
    """第一次分别返回低质量和异常，第二次根据反馈生成可接受注释。"""

    def __init__(self, artifacts: ArtifactStore) -> None:
        self.artifacts = artifacts
        self.feedback_seen: dict[str, list[str]] = {}

    def annotate(self, request: SemanticAnnotationRequest) -> str:
        unit = request.unit
        attempt = request.attempt
        self.feedback_seen[unit.unit_id] = request.quality_feedback
        if unit.unit_id == "incomplete" and attempt == 1:
            raise RuntimeError("模拟模型请求未完成")
        symbol = unit.symbol_ids[0]
        response = ClusterAnnotationResponse(
            unit_id=unit.unit_id,
            annotations=[
                FunctionAnnotation(
                    symbol_id=symbol,
                    file_path=f"{symbol}.m",
                    start_line=1,
                    end_line=3,
                    summary="低质量初稿" if attempt == 1 else "根据反馈完成的函数语义",
                    confidence=0.2 if attempt == 1 else 0.9,
                    evidence=[
                        SourceEvidence(
                            file_path=f"{symbol}.m",
                            start_line=1,
                            end_line=3,
                            source_hash=f"hash-{symbol}",
                        )
                    ],
                )
            ],
        )
        reference = self.artifacts.write_model(
            request.job_id,
            f"test-{unit.unit_id}-attempt-{attempt}.json",
            response,
        )
        return reference


def _aggregate_response(_system: str, _prompt: str, response_model):
    if response_model.__name__ == "FileAnnotationDraft":
        return {"role": "测试文件", "risks": [], "confidence": 0.9}
    assert response_model is ProjectAnnotationDraft
    return {
        "purpose": "验证语义循环",
        "usage": "运行测试入口",
        "risks": [],
        "confidence": 0.9,
    }


def test_low_quality_and_incomplete_clusters_are_requeued(tmp_path: Path) -> None:
    artifacts = ArtifactStore(tmp_path / "artifacts")
    job_id = "semanticlooptest"
    functions = [
        FunctionInfo(
            name=name,
            qualified_name=name,
            file_path=f"{name}.m",
            start_line=1,
            end_line=3,
        )
        for name in ("low", "missing")
    ]
    analysis_ref = artifacts.write_model(
        job_id,
        "analysis.json",
        AnalysisResult(
            project_root=str(tmp_path),
            functions=functions,
            entry_points=["low"],
        ),
    )
    units_ref = artifacts.write_model(
        job_id,
        "units.json",
        SemanticWorkUnits(
            project_root=str(tmp_path),
            token_budget=2000,
            units=[
                SemanticWorkUnit(
                    unit_id="low-quality",
                    symbol_ids=["low"],
                    estimated_tokens=20,
                ),
                SemanticWorkUnit(
                    unit_id="incomplete",
                    symbol_ids=["missing"],
                    estimated_tokens=20,
                ),
            ],
        ),
    )
    preparation_ref = artifacts.write_model(
        job_id,
        "preparation.json",
        SemanticPreparationBundle(
            job_id=job_id,
            project_root=str(tmp_path),
            scan_reference="unused-scan.json",
            analysis_reference=analysis_ref,
            code_tree_reference="unused-tree.json",
            work_units_reference=units_ref,
            token_budget=2000,
        ),
    )
    annotator = _SequenceAnnotator(artifacts)
    graph = SemanticAnnotationGraph(
        artifacts,
        annotator,
        SemanticAggregator(
            FakeStructuredLLMClient(_aggregate_response)
        ),
        confidence_threshold=0.6,
        max_attempts=2,
    )

    events: list[SemanticProgressEvent] = []
    state = graph.invoke(preparation_ref, event_handler=events.append)
    report = artifacts.read_model(
        state["quality_report_ref"], SemanticQualityReport
    )

    assert report.accepted_unit_ids == ["incomplete", "low-quality"]
    assert report.low_quality_unit_ids == ["low-quality"]
    assert report.incomplete_unit_ids == ["incomplete"]
    assert report.manual_review_unit_ids == []
    assert state["attempts"] == {"low-quality": 2, "incomplete": 2}
    assert annotator.feedback_seen["low-quality"]
    assert annotator.feedback_seen["incomplete"] == ["模拟模型请求未完成"]
    assert [event.sequence for event in events] == list(
        range(1, len(events) + 1)
    )
    assert any(
        event.node == "quality_check" and event.phase == "retrying"
        for event in events
    )
    assert events[-1].node == "aggregate"
    assert events[-1].phase == "completed"


def test_file_aggregation_runs_concurrently_and_preserves_order(
    tmp_path: Path,
) -> None:
    barrier = Barrier(2)
    lock = Lock()
    active = 0
    peak_active = 0

    def aggregate_response(_system: str, _prompt: str, response_model):
        nonlocal active, peak_active
        if response_model is FileAnnotationDraft:
            with lock:
                active += 1
                peak_active = max(peak_active, active)
            try:
                barrier.wait(timeout=2)
            finally:
                with lock:
                    active -= 1
            return {"role": "并发文件", "risks": [], "confidence": 0.9}
        assert response_model is ProjectAnnotationDraft
        return {
            "purpose": "验证文件级并发",
            "usage": "运行入口函数",
            "risks": [],
            "confidence": 0.9,
        }

    functions = [
        FunctionInfo(
            name=name,
            qualified_name=name,
            file_path=f"{name}.m",
            start_line=1,
            end_line=3,
        )
        for name in ("alpha", "beta")
    ]
    annotations = [
        FunctionAnnotation(
            symbol_id=item.qualified_name,
            file_path=item.file_path,
            start_line=item.start_line,
            end_line=item.end_line,
            summary="测试函数",
            confidence=0.9,
            evidence=[
                SourceEvidence(
                    file_path=item.file_path,
                    start_line=item.start_line,
                    end_line=item.end_line,
                    source_hash=f"hash-{item.qualified_name}",
                )
            ],
        )
        for item in functions
    ]
    index = SemanticAggregator(
        FakeStructuredLLMClient(aggregate_response),
        max_concurrency=2,
    ).aggregate(
        AnalysisResult(project_root=str(tmp_path), functions=functions),
        [ClusterAnnotationResponse(unit_id="both", annotations=annotations)],
    )

    assert peak_active == 2
    assert [item.file_path for item in index.files] == ["alpha.m", "beta.m"]
