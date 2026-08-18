"""
Description: Worker-2 解析独立文件分片，并将多个分片聚合为 ScanResult。
References: MaxxMatlabParser、ScannerAgent、ArtifactStore、domain.models、argparse。
Referenced By: Orchestrator WorkerPool、分片流水线测试；可通过 python -m 独立演示。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence
from uuid import uuid4

from matlab_refactor_agent.workers.matlab_parser import MaxxMatlabParser
from matlab_refactor_agent.domain.enums import WorkerKind
from matlab_refactor_agent.domain.exceptions import ArtifactError, OrchestrationError
from matlab_refactor_agent.domain.models import (
    MatlabFileManifest,
    ParseChunkResult,
    ScanResult,
)
from matlab_refactor_agent.domain.orchestration import TaskEnvelope, WorkerResult
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore

from .base import BaseWorker, WorkerContext
from .scanner_agent import ScannerAgent


class ParserAgent(BaseWorker):
    """作用：执行 Worker-2 分片解析与 fan-in 聚合；输入：manifest/分片引用；输出：chunk 或 scan artifact；数据流：文件清单 -> maxx 解析 -> 分片 -> ScanResult。"""

    def __init__(self) -> None:
        """作用：装配 MATLAB 单文件解析器；输入：已安装 maxx；输出：ParserAgent；数据流：Worker 工厂 -> MaxxMatlabParser。"""

        self._parser = MaxxMatlabParser()

    @property
    def kind(self) -> WorkerKind:
        """作用：声明 Worker-2 类型；输入：实例；输出：PARSER；数据流：WorkerPool 注册 -> 任务路由。"""

        return WorkerKind.PARSER

    def execute(self, task: TaskEnvelope, context: WorkerContext) -> WorkerResult:
        """作用：按 operation 路由解析或聚合；输入：TaskEnvelope；输出：WorkerResult；数据流：payload.operation -> parse_chunk/aggregate。"""

        operation = str(task.payload.get("operation", ""))
        if operation == "parse_chunk":
            return self._parse_chunk(task, context)
        if operation == "aggregate":
            return self._aggregate(task, context)
        raise OrchestrationError(f"ParserAgent 不支持 operation: {operation}")

    def _parse_chunk(
        self, task: TaskEnvelope, context: WorkerContext
    ) -> WorkerResult:
        """作用：解析一个独立文件批次；输入：manifest 引用、文件列表和 chunk_index；输出：ParseChunkResult artifact；数据流：相对路径 -> maxx -> 分片 JSON。"""

        manifest = context.artifact_store.read_model(
            str(task.payload["file_manifest"]), MatlabFileManifest
        )
        chunk_index = int(task.payload["chunk_index"])
        requested = [str(item) for item in task.payload.get("files", [])]
        allowed = set(manifest.files)
        root = Path(manifest.project_root).resolve()
        files = []
        for relative in requested:
            if relative not in allowed:
                raise ArtifactError(f"分片包含 manifest 外文件: {relative}")
            path = (root / relative).resolve()
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise ArtifactError(f"分片文件路径越界: {relative}") from exc
            if not path.is_file():
                raise ArtifactError(f"分片文件不存在: {path}")
            files.append(self._parser.parse_file(path, root))
        result = ParseChunkResult(
            project_root=manifest.project_root,
            chunk_index=chunk_index,
            files=files,
            diagnostics=[message for item in files for message in item.diagnostics],
        )
        key = f"parse_chunk_{chunk_index}"
        reference = context.artifact_store.write_model(
            task.job_id, f"parse-chunk-{chunk_index:05d}.json", result
        )
        return WorkerResult(
            task_id=task.task_id,
            success=True,
            artifacts={key: reference},
            metrics={
                "chunk_index": chunk_index,
                "file_count": len(files),
                "function_count": sum(len(item.functions) for item in files),
            },
        )

    def _aggregate(self, task: TaskEnvelope, context: WorkerContext) -> WorkerResult:
        """作用：聚合所有解析分片；输入：manifest 和 chunk artifact 引用；输出：ScanResult artifact；数据流：ParseChunkResult 列表 -> 完整性检查/排序 -> scan-result.json。"""

        manifest = context.artifact_store.read_model(
            str(task.payload["file_manifest"]), MatlabFileManifest
        )
        references = [str(item) for item in task.payload.get("chunk_results", [])]
        chunks = [
            context.artifact_store.read_model(reference, ParseChunkResult)
            for reference in references
        ]
        parsed_files = [item for chunk in chunks for item in chunk.files]
        parsed_paths = [item.path for item in parsed_files]
        if len(parsed_paths) != len(set(parsed_paths)):
            raise ArtifactError("Parser 聚合发现重复文件")
        if set(parsed_paths) != set(manifest.files):
            missing = sorted(set(manifest.files) - set(parsed_paths))
            unexpected = sorted(set(parsed_paths) - set(manifest.files))
            raise ArtifactError(
                f"Parser 聚合文件不完整，missing={missing}, unexpected={unexpected}"
            )
        result = ScanResult(
            project_root=manifest.project_root,
            files=sorted(parsed_files, key=lambda item: item.path),
            excluded_count=manifest.excluded_count,
        )
        reference = context.artifact_store.write_model(
            task.job_id, "scan-result.json", result
        )
        return WorkerResult(
            task_id=task.task_id,
            success=True,
            artifacts={"scan_result": reference},
            metrics={
                "chunk_count": len(chunks),
                "file_count": len(result.files),
                "function_count": result.function_count,
            },
        )


def main(argv: Sequence[str] | None = None) -> int:
    """作用：独立演示 Worker-2 分片和聚合；输入：项目、分片大小和 artifact 目录；输出：聚合 WorkerResult JSON；数据流：CLI -> manifest -> 多个 parse_chunk -> aggregate -> stdout。"""

    parser = argparse.ArgumentParser(description="演示 Worker-2 ParserAgent")
    parser.add_argument("project", type=Path, help="MATLAB 项目目录")
    parser.add_argument("--chunk-size", type=int, default=100)
    parser.add_argument(
        "--artifact-dir", type=Path, default=Path("var/worker-demos")
    )
    parser.add_argument("--exclude", action="append", default=[])
    args = parser.parse_args(argv)
    if args.chunk_size < 1:
        parser.error("--chunk-size 必须大于 0")

    job_id = uuid4().hex
    context = WorkerContext(ArtifactStore(args.artifact_dir))
    scanner_task = TaskEnvelope(
        job_id=job_id,
        worker_kind=WorkerKind.SCANNER,
        payload={"project_root": str(args.project)},
    )
    scanner_result = ScannerAgent(args.exclude).execute(scanner_task, context)
    manifest_reference = scanner_result.artifacts["file_manifest"]
    manifest = context.artifact_store.read_model(
        manifest_reference, MatlabFileManifest
    )
    worker = ParserAgent()
    chunk_references: list[str] = []
    for chunk_index, offset in enumerate(
        range(0, len(manifest.files), args.chunk_size)
    ):
        chunk_task = TaskEnvelope(
            job_id=job_id,
            worker_kind=WorkerKind.PARSER,
            payload={
                "operation": "parse_chunk",
                "file_manifest": manifest_reference,
                "chunk_index": chunk_index,
                "files": manifest.files[offset : offset + args.chunk_size],
            },
        )
        chunk_result = worker.execute(chunk_task, context)
        chunk_references.extend(chunk_result.artifacts.values())
    aggregate_task = TaskEnvelope(
        job_id=job_id,
        worker_kind=WorkerKind.PARSER,
        payload={
            "operation": "aggregate",
            "file_manifest": manifest_reference,
            "chunk_results": chunk_references,
        },
    )
    result = worker.execute(aggregate_task, context)
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
