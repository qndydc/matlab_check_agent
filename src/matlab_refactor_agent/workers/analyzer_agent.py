"""
Description: Worker-3 读取扫描 artifact，构建依赖图并写出分析 artifact。
References: DependencyAnalyzer、ArtifactStore、domain.orchestration、argparse。
Referenced By: Orchestrator WorkerPool；可通过 python -m 独立演示。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence
from uuid import uuid4

from matlab_refactor_agent.workers.dependency_analysis import DependencyAnalyzer
#该analyser的职责不是重新实现图算法，而是把 DependencyAnalyzer 接入统一 Worker 系统。
from matlab_refactor_agent.domain.enums import WorkerKind
from matlab_refactor_agent.domain.models import DomainModel, ScanResult
from matlab_refactor_agent.domain.orchestration import TaskEnvelope, WorkerResult
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore

from .base import BaseWorker, WorkerContext


class AnalyzerPayload(DomainModel):
    scan_result: str


class AnalyzerWorker(BaseWorker):
    """作用：执行 Worker-3 图分析与指标计算；输入：scan artifact；输出：analysis artifact；数据流：ScanResult 引用 -> NetworkX -> AnalysisResult JSON。"""

    def __init__(self, entry_points: list[str]) -> None:
        """作用：配置分析 Worker；输入：手工入口点；输出：AnalyzerWorker；数据流：项目配置 -> DependencyAnalyzer 调用。"""

        self._entry_points = entry_points
        self._analyzer = DependencyAnalyzer()

    @property
    def kind(self) -> WorkerKind:
        """作用：声明 Worker-3 类型；输入：实例；输出：ANALYZER；数据流：WorkerPool 注册 -> 任务路由。"""

        return WorkerKind.ANALYZER

    def execute(self, task: TaskEnvelope, context: WorkerContext) -> WorkerResult:
        """作用：分析持久化扫描结果；输入：scan_result 引用；输出：analysis artifact；数据流：artifact -> 调用图/指标 -> artifact。"""

        scan = context.artifact_store.read_model(
            str(task.payload["scan_result"]), ScanResult
        )
        # analyze为DependencyAnalyzer的核心方法，返回AnalysisResult
        result = self._analyzer.analyze(scan, self._entry_points)
        reference = context.artifact_store.write_model(
            task.job_id, "analysis-result.json", result
        ) #输出json文件，供后续可视化或其他分析使用
        return WorkerResult(
            task_id=task.task_id,
            success=True,
            artifacts={"analysis_result": reference},
            metrics={
                "node_count": len(result.functions),
                "edge_count": len(result.dependencies),
                "cycle_count": len(result.cycle_clusters),
                "orphan_count": len(result.orphans),
            },
        )

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return AnalyzerPayload.model_validate(payload).model_dump(mode="python")


def main(argv: Sequence[str] | None = None) -> int:
    """作用：独立演示 Worker-3 依赖分析；输入：scan-result 引用、入口点和 artifact 目录；输出：WorkerResult JSON；数据流：CLI -> ScanResult artifact -> AnalyzerAgent -> analysis artifact -> stdout。"""

    parser = argparse.ArgumentParser(description="演示 Worker-3 AnalyzerWorker")
    parser.add_argument("scan_result", type=Path, help="Worker-2 生成的 scan-result.json")
    parser.add_argument(
        "--artifact-dir", type=Path, default=Path("var/worker-demos")
    )
    parser.add_argument("--entry-point", action="append", default=[])
    args = parser.parse_args(argv)
    task = TaskEnvelope(
        job_id=uuid4().hex,
        worker_kind=WorkerKind.ANALYZER,
        payload={"scan_result": str(args.scan_result.resolve())},
    )
    result = AnalyzerWorker(args.entry_point).execute(
        task, WorkerContext(ArtifactStore(args.artifact_dir))
    )
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())


# 兼容 0.1 版导入路径；新代码使用 AnalyzerWorker。
AnalyzerAgent = AnalyzerWorker
