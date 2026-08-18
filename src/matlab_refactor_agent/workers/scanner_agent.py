"""
Description: Worker-1 扫描 MATLAB 工程并写出轻量文件清单。
References: MatlabFileDiscovery、ArtifactStore、argparse。
Referenced By: Orchestrator WorkerPool；可通过 python -m 独立演示。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence
from uuid import uuid4

from matlab_refactor_agent.workers.scanning import MatlabFileDiscovery
from matlab_refactor_agent.domain.enums import WorkerKind
from matlab_refactor_agent.domain.orchestration import TaskEnvelope, WorkerResult
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore

from .base import BaseWorker, WorkerContext


class ScannerAgent(BaseWorker):
    """作用：执行 Worker-1 文件发现；输入：项目路径任务；输出：manifest artifact；数据流：项目目录 -> 过滤/排序 -> MatlabFileManifest JSON。"""

    def __init__(self, exclude_patterns: list[str]) -> None:
        """作用：装配文件发现能力；输入：排除规则；输出：ScannerAgent；数据流：项目配置 -> MatlabFileDiscovery。"""

        self._discovery = MatlabFileDiscovery(exclude_patterns)

    @property
    def kind(self) -> WorkerKind:
        """作用：声明 Worker-1 类型；输入：实例；输出：SCANNER；数据流：WorkerPool 注册 -> 任务路由。"""

        return WorkerKind.SCANNER

    def execute(self, task: TaskEnvelope, context: WorkerContext) -> WorkerResult:
        """作用：扫描 MATLAB 文件路径；输入：project_root payload；输出：file_manifest 引用；数据流：路径 -> Discovery -> artifact。"""

        project_root = Path(str(task.payload["project_root"]))
        result = self._discovery.discover(project_root)
        reference = context.artifact_store.write_model(
            task.job_id, "file-manifest.json", result
        )
        return WorkerResult(
            task_id=task.task_id,
            success=True,
            artifacts={"file_manifest": reference}, #文件清单的保存位置
            metrics={
                "file_count": result.file_count,
                "excluded_count": result.excluded_count,
            },
        )


def main(argv: Sequence[str] | None = None) -> int:
    """作用：独立演示 Worker-1 文件发现；输入：项目、artifact 目录及排除规则；输出：WorkerResult JSON 和退出码；数据流：CLI -> ScannerAgent -> file-manifest artifact -> stdout。"""

    parser = argparse.ArgumentParser(description="演示 Worker-1 ScannerAgent")
    parser.add_argument("project", type=Path, help="MATLAB 项目目录")
    parser.add_argument(
        "--artifact-dir", type=Path, default=Path("var/worker-demos")
    )
    parser.add_argument("--exclude", action="append", default=[])
    args = parser.parse_args(argv)
    job_id = uuid4().hex
    task = TaskEnvelope(
        job_id=job_id,
        worker_kind=WorkerKind.SCANNER,
        payload={"project_root": str(args.project)},
    )
    result = ScannerAgent(args.exclude).execute(
        task, WorkerContext(ArtifactStore(args.artifact_dir))
    )
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
