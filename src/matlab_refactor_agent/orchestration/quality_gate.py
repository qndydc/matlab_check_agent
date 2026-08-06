"""
Description: 校验 Scanner、Parser 和 Analyzer 阶段 artifact 及图结构不变量。
References: ArtifactStore、domain.models、domain.exceptions.QualityGateError。
Referenced By: Orchestrator 和质量测试。
"""

from matlab_refactor_agent.domain.enums import WorkerKind
from matlab_refactor_agent.domain.exceptions import QualityGateError
from matlab_refactor_agent.domain.models import (
    AnalysisResult,
    MatlabFileManifest,
    ParseChunkResult,
    ScanResult,
)
from matlab_refactor_agent.domain.orchestration import TaskEnvelope, WorkerResult
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore


class QualityGate:
    """作用：校验每阶段 Worker 输出；输入：任务、结果和 artifact；输出：通过或 QualityGateError；数据流：WorkerResult -> 模型/不变量校验 -> 下一阶段。"""

    def __init__(self, artifact_store: ArtifactStore) -> None:
        """作用：配置 artifact 读取能力；输入：ArtifactStore；输出：QualityGate；数据流：Orchestrator 装配 -> 阶段校验。"""

        self._artifacts = artifact_store

    def validate(self, task: TaskEnvelope, result: WorkerResult) -> None:
        """作用：分派阶段质量规则；输入：TaskEnvelope 和 WorkerResult；输出：无或异常；数据流：worker_kind -> 专用模型校验。"""

        if not result.success:
            detail = "; ".join(result.diagnostics) or "Worker 返回失败"
            raise QualityGateError(detail)
        if str(task.worker_kind) == str(WorkerKind.SCANNER):
            self._validate_scan(result)
        elif str(task.worker_kind) == str(WorkerKind.PARSER):
            self._validate_parser(task, result)
        elif str(task.worker_kind) == str(WorkerKind.ANALYZER):
            self._validate_analysis(result)

    def _validate_scan(self, result: WorkerResult) -> None:
        """作用：校验扫描清单；输入：WorkerResult；输出：无或异常；数据流：manifest artifact -> 相对路径唯一性/排序检查。"""

        reference = result.artifacts.get("file_manifest")
        if reference is None:
            raise QualityGateError("ScannerAgent 缺少 file_manifest artifact")
        manifest = self._artifacts.read_model(reference, MatlabFileManifest)
        if len(manifest.files) != len(set(manifest.files)):
            raise QualityGateError("文件清单包含重复路径")
        if manifest.files != sorted(manifest.files):
            raise QualityGateError("文件清单未稳定排序")

    def _validate_parser(
        self, task: TaskEnvelope, result: WorkerResult
    ) -> None:
        """作用：校验解析分片或聚合结果；输入：Parser 任务和结果；输出：无或异常；数据流：operation -> ParseChunkResult/ScanResult 不变量。"""

        operation = str(task.payload.get("operation", ""))
        if operation == "parse_chunk":
            chunk_index = int(task.payload["chunk_index"])
            reference = result.artifacts.get(f"parse_chunk_{chunk_index}")
            if reference is None:
                raise QualityGateError("ParserAgent 缺少分片 artifact")
            chunk = self._artifacts.read_model(reference, ParseChunkResult)
            paths = [item.path for item in chunk.files]
            requested = [str(item) for item in task.payload.get("files", [])]
            if chunk.chunk_index != chunk_index or paths != requested:
                raise QualityGateError("解析分片索引或文件顺序不匹配")
            if len(paths) != len(set(paths)):
                raise QualityGateError("解析分片包含重复文件")
            return
        if operation == "aggregate":
            reference = result.artifacts.get("scan_result")
            if reference is None:
                raise QualityGateError("ParserAgent 聚合缺少 scan_result artifact")
            scan = self._artifacts.read_model(reference, ScanResult)
            paths = [item.path for item in scan.files]
            if len(paths) != len(set(paths)) or paths != sorted(paths):
                raise QualityGateError("Parser 聚合结果重复或未排序")
            return
        raise QualityGateError(f"未知 Parser operation: {operation}")

    def _validate_analysis(self, result: WorkerResult) -> None:
        """作用：校验分析阶段图不变量；输入：WorkerResult；输出：无或异常；数据流：analysis artifact -> 节点/边/循环引用检查。"""

        reference = result.artifacts.get("analysis_result")
        if reference is None:
            raise QualityGateError("AnalyzerAgent 缺少 analysis_result artifact")
        analysis = self._artifacts.read_model(reference, AnalysisResult)
        node_ids = [item.qualified_name for item in analysis.functions]
        if len(node_ids) != len(set(node_ids)):
            raise QualityGateError("分析结果包含重复节点 ID")
        known = set(node_ids)
        for edge in analysis.dependencies:
            if edge.source not in known or edge.target not in known:
                raise QualityGateError(
                    f"依赖边引用未知节点: {edge.source} -> {edge.target}"
                )
        for cycle in analysis.cycles:
            unknown = set(cycle) - known
            if unknown:
                raise QualityGateError(f"循环依赖引用未知节点: {sorted(unknown)}")
