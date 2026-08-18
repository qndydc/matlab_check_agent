"""
Description: 使用结构化 LLM 将函数注解聚合为中文文件级和项目级语义。
References: StructuredLLMClient、domain.models、domain.semantics。
Referenced By: LangGraphWorkflow 和语义聚合测试。
"""

from __future__ import annotations

import json
from collections import defaultdict

from matlab_refactor_agent.domain.exceptions import OrchestrationError
from matlab_refactor_agent.domain.models import AnalysisResult
from matlab_refactor_agent.domain.semantics import (
    ClusterAnnotationResponse,
    FileAnnotation,
    FunctionAnnotation,
    ProjectAnnotation,
    SemanticConflict,
    SemanticIndex,
)
from matlab_refactor_agent.infrastructure.llm import StructuredLLMClient

FILE_SYSTEM_PROMPT = (
    "你是 MATLAB 文件语义聚合 Agent。根据同一文件内的函数注解，概括该文件在项目中的职责。"
    "role 和 risks 必须使用中文；函数名和 MATLAB 专有名词可以保留原文。"
    "不得添加输入中没有依据的功能。"
)

PROJECT_SYSTEM_PROMPT = (
    "你是 MATLAB 项目语义聚合 Agent。根据文件级语义、入口点和依赖图摘要，生成项目级说明。"
    "purpose 必须清楚描述项目用来做什么；usage 必须说明用户如何使用该项目，包括合理的入口或调用顺序。"
    "purpose、usage 和 risks 必须使用中文；函数名、文件名和 MATLAB 专有名词可以保留原文。"
    "无法从上下文确认的使用步骤必须明确标为待确认，不得臆测。"
)


class SemanticAnnotationAggregator:
    """作用：校验函数注解并调用 LLM 生成中文文件级和项目级语义索引。"""

    def __init__(
        self,
        client: StructuredLLMClient,
        low_confidence_threshold: float = 0.6,
    ) -> None:
        """作用：注入结构化 LLM 客户端和低置信度阈值。"""

        self._client = client
        self._threshold = low_confidence_threshold

    def aggregate(
        self,
        analysis: AnalysisResult,
        responses: list[ClusterAnnotationResponse],
    ) -> SemanticIndex:
        """作用：汇总函数响应、生成文件/项目语义并输出完整三级索引。"""

        annotations, conflicts = self._collect_function_annotations(
            analysis, responses
        )
        files = self._file_annotations(annotations)
        project = self._project_annotation(analysis, files)
        return SemanticIndex(
            project_root=analysis.project_root,
            functions=annotations,
            core_functions=sorted(
                item.symbol_id for item in annotations if item.is_algorithm_core
            ),
            files=files,
            project=project,
            conflicts=sorted(conflicts, key=lambda item: (item.symbol_id, item.reason)),
        )

    def _collect_function_annotations(
        self,
        analysis: AnalysisResult,
        responses: list[ClusterAnnotationResponse],
    ) -> tuple[list[FunctionAnnotation], list[SemanticConflict]]:
        """作用：去重函数注解，并产生缺失和低置信度复核项。"""

        annotations: dict[str, FunctionAnnotation] = {}
        conflicts: list[SemanticConflict] = []
        for response in responses:
            for annotation in response.annotations:
                if annotation.symbol_id in annotations:
                    conflicts.append(
                        SemanticConflict(
                            symbol_id=annotation.symbol_id,
                            reason="多个函数簇返回了重复注解",
                        )
                    )
                    continue
                annotations[annotation.symbol_id] = annotation
                if annotation.confidence < self._threshold:
                    conflicts.append(
                        SemanticConflict(
                            symbol_id=annotation.symbol_id,
                            reason=(
                                f"置信度 {annotation.confidence:.2f} 低于阈值 "
                                f"{self._threshold:.2f}"
                            ),
                        )
                    )
        expected = {item.qualified_name for item in analysis.functions}
        for symbol in sorted(expected - annotations.keys()):
            conflicts.append(
                SemanticConflict(symbol_id=symbol, reason="缺少函数语义注解")
            )
        return (
            [annotations[symbol] for symbol in sorted(annotations)],
            conflicts,
        )

    def _file_annotations(
        self, annotations: list[FunctionAnnotation]
    ) -> list[FileAnnotation]:
        """作用：按文件分组函数注解，并为每个文件调用一次 LLM 生成职责说明。"""

        by_file: dict[str, list[FunctionAnnotation]] = defaultdict(list)
        for annotation in annotations:
            by_file[annotation.file_path].append(annotation)
        files: list[FileAnnotation] = []
        for file_path in sorted(by_file):
            items = sorted(by_file[file_path], key=lambda item: item.symbol_id)
            expected_symbols = [item.symbol_id for item in items]
            response = self._client.complete(
                system_prompt=FILE_SYSTEM_PROMPT,
                user_prompt=json.dumps(
                    {
                        "file_path": file_path,
                        "functions": [
                            item.model_dump(mode="json", exclude={"evidence"})
                            for item in items
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                response_model=FileAnnotation,
            )
            if response.file_path != file_path:
                raise OrchestrationError(
                    f"文件级语义返回了错误路径: {response.file_path} != {file_path}"
                )
            if sorted(response.function_symbols) != expected_symbols:
                raise OrchestrationError(
                    f"文件级语义返回了错误函数集合: {file_path}"
                )
            files.append(response)
        return files

    def _project_annotation(
        self,
        analysis: AnalysisResult,
        files: list[FileAnnotation],
    ) -> ProjectAnnotation:
        """作用：根据文件语义和调用图摘要生成项目用途与使用方法。"""

        expected_files = [item.file_path for item in files]
        response = self._client.complete(
            system_prompt=PROJECT_SYSTEM_PROMPT,
            user_prompt=json.dumps(
                {
                    "project_root": analysis.project_root,
                    "entry_points": analysis.entry_points,
                    "files": [item.model_dump(mode="json") for item in files],
                    "cycles": analysis.cycles,
                    "unresolved_calls": analysis.unresolved_calls,
                },
                ensure_ascii=False,
                indent=2,
            ),
            response_model=ProjectAnnotation,
        )
        if response.entry_points != analysis.entry_points:
            raise OrchestrationError("项目级语义修改了确定性入口点")
        if sorted(response.files) != expected_files:
            raise OrchestrationError("项目级语义返回了错误文件集合")
        if not response.usage.strip():
            raise OrchestrationError("项目级语义缺少使用方法")
        return response
