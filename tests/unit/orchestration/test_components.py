"""
Description: 验证 ArtifactStore、质量门禁和确定性 Worker 路由基础行为。
References: orchestration 组件、domain.orchestration、pytest。
Referenced By: pytest 测试发现。
"""

from pathlib import Path

import pytest

from matlab_refactor_agent.domain.enums import WorkerKind
from matlab_refactor_agent.domain.exceptions import ArtifactError, QualityGateError
from matlab_refactor_agent.domain.models import AnalysisResult, MatlabFileInfo, ScanResult
from matlab_refactor_agent.domain.semantics import SemanticWorkUnits
from matlab_refactor_agent.domain.orchestration import TaskEnvelope, WorkerResult
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore
from matlab_refactor_agent.orchestration import QualityGate, WorkerPool
from matlab_refactor_agent.workers.base import BaseWorker, WorkerContext


class _TestWorker(BaseWorker):
    """作用：验证 WorkerPool 只负责类型路由；输入：任务；输出：成功结果。"""

    @property
    def kind(self) -> WorkerKind:
        """作用：声明测试 Worker 类型；输入：实例；输出：SCANNER；数据流：WorkerPool 注册 -> 路由。"""

        return WorkerKind.SCANNER

    def execute(self, task: TaskEnvelope, context: WorkerContext) -> WorkerResult:
        """作用：返回成功结果；输入：任务与上下文；输出：WorkerResult；数据流：WorkerPool -> 测试 Worker。"""

        return WorkerResult(task_id=task.task_id, success=True)


def test_artifact_store_round_trip_and_boundary(tmp_path: Path) -> None:
    """作用：验证 artifact 隔离和读取；输入：临时根目录与 ScanResult；输出：断言/越界异常；数据流：模型 -> Job JSON -> 模型。"""

    store = ArtifactStore(tmp_path / "artifacts")
    original = ScanResult(project_root="/project")

    reference = store.write_model("job1", "scan.json", original)

    assert store.read_model(reference, ScanResult) == original
    with pytest.raises(ArtifactError):
        store.read_model(str(tmp_path / "outside.json"), ScanResult)


def test_worker_pool_routes_deterministic_stage(tmp_path: Path) -> None:
    """作用：验证 WorkerPool 类型路由；输入：Scanner 任务；输出：成功 WorkerResult。"""

    pool = WorkerPool(max_workers=2)
    pool.register(_TestWorker())
    task = TaskEnvelope(job_id="job", worker_kind=WorkerKind.SCANNER)

    outcome = pool.execute(
        task, WorkerContext(artifact_store=ArtifactStore(tmp_path / "artifacts"))
    )

    assert isinstance(outcome, WorkerResult)
    assert outcome.success


def test_semantic_preflight_blocks_failed_parser_files(tmp_path: Path) -> None:
    """作用：验证解析状态为 failed 的文件会在任何语义模型调用前被质量门阻断。"""

    gate = QualityGate(ArtifactStore(tmp_path / "artifacts"))
    scan = ScanResult(
        project_root=str(tmp_path),
        files=[MatlabFileInfo(path="broken.m", parse_status="failed")],
    )
    analysis = AnalysisResult(project_root=str(tmp_path))
    units = SemanticWorkUnits(project_root=str(tmp_path), token_budget=32_768)

    with pytest.raises(QualityGateError, match="存在解析失败文件"):
        gate.validate_semantic_preflight(scan, analysis, units, [])
