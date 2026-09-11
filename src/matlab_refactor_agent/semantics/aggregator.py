"""
Description: 将函数簇注释聚合成中文文件级和项目级语义，并记录覆盖冲突。
References: StructuredLLMClient、domain.models、domain.semantics。
Referenced By: SemanticAnnotationPipeline 和兼容聚合器导入。
"""

from __future__ import annotations

import json
from collections import defaultdict
from hashlib import sha256
from typing import TypeVar

from pydantic import BaseModel

from matlab_refactor_agent.domain.exceptions import OrchestrationError
from matlab_refactor_agent.domain.models import AnalysisResult
from matlab_refactor_agent.domain.semantics import (
    ClusterAnnotationResponse,
    FileAnnotation,
    FileAnnotationDraft,
    FunctionAnnotation,
    ProjectAnnotation,
    ProjectAnnotationDraft,
    SemanticConflict,
    SemanticIndex,
)
from matlab_refactor_agent.infrastructure.llm import StructuredLLMClient
from matlab_refactor_agent.orchestration.execution_pool import global_heavy_pool
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore
from matlab_refactor_agent.orchestration.call_lifecycle import CallObservationRecorder

DraftT = TypeVar("DraftT", bound=BaseModel)


FILE_SYSTEM_PROMPT = (
    "根据同一文件内的函数注解，概括该文件在项目中的职责。"
    "role 和 risks 必须使用中文；函数名和 MATLAB 专有名词可以保留原文。"
    "只生成 role、risks 和 confidence；不要判断 file_path 与 function_symbols。"
    "不得添加输入中没有依据的功能。"
)

PROJECT_SYSTEM_PROMPT = (
    "根据文件级语义、入口点和依赖图摘要生成项目级说明。"
    "purpose 描述项目用途；usage 说明合理入口或调用顺序。"
    "purpose、usage 和 risks 必须使用中文；专有名词可以保留原文。"
    "只生成 purpose、usage、risks 和 confidence。"
    "无法确认的使用步骤必须明确标为待确认。"
)


class SemanticAggregator:
    """校验函数覆盖，并生成文件级与项目级说明。"""

    def __init__(
        self,
        client: StructuredLLMClient,
        low_confidence_threshold: float = 0.6,
        *,
        artifacts: ArtifactStore | None = None,
        job_id: str | None = None,
        debug_model: bool = False,
        max_concurrency: int = 1,
    ) -> None:
        self._client = client
        self._threshold = low_confidence_threshold
        self._artifacts = artifacts
        self._job_id = job_id
        self._debug_model = debug_model
        self._max_concurrency = max(1, max_concurrency)

    def _complete(self, *, system_prompt: str, user_prompt: str,
                  response_model: type[DraftT]) -> DraftT:
        key = sha256((system_prompt + user_prompt + response_model.__name__).encode("utf-8")).hexdigest()
        name = f"semantic-aggregate-{key}.json"
        if self._artifacts is not None and self._job_id is not None:
            path = self._artifacts.root / self._job_id / name
            if path.is_file():
                DEBUG_MODEL = self._debug_model
                if DEBUG_MODEL:
                    print(
                        f"[DEBUG_MODEL][SEMANTIC][AGGREGATE][CACHE] "
                        f"response_model={response_model.__name__} artifact={path.name}",
                        flush=True,
                    )
                return self._artifacts.read_model(str(path), response_model)
        DEBUG_MODEL = self._debug_model
        if DEBUG_MODEL:
            print(
                f"[DEBUG_MODEL][SEMANTIC][AGGREGATE][MODEL] "
                f"response_model={response_model.__name__} prompt_chars={len(user_prompt)}",
                flush=True,
            )
        draft = global_heavy_pool.run(
            self._client.complete,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_model=response_model,
            tool_name=f"llm.semantic.aggregate.{response_model.__name__}",
            observation_callback=(
                CallObservationRecorder(self._artifacts, self._job_id)
                if self._artifacts is not None and self._job_id is not None
                else None
            ),
        )
        if isinstance(draft, ProjectAnnotationDraft) and not draft.usage.strip():
            raise OrchestrationError("项目级语义缺少使用方法")
        if self._artifacts is not None and self._job_id is not None:
            self._artifacts.write_model(self._job_id, name, draft)
        return draft

    def aggregate(
        self,
        analysis: AnalysisResult,
        responses: list[ClusterAnnotationResponse],
    ) -> SemanticIndex:
        annotations, conflicts = self._collect_function_annotations(
            analysis, responses
        )
        files = self._file_annotations(annotations)
        project = self._project_annotation(analysis, files)
        return SemanticIndex(
            project_root=analysis.project_root,
            functions=annotations,
            core_functions=sorted(
                item.symbol_id
                for item in annotations
                if item.is_algorithm_core
            ),
            files=files,
            project=project,
            conflicts=sorted(
                conflicts, key=lambda item: (item.symbol_id, item.reason)
            ),
        )

    def _collect_function_annotations(
        self,
        analysis: AnalysisResult,
        responses: list[ClusterAnnotationResponse],
    ) -> tuple[list[FunctionAnnotation], list[SemanticConflict]]:
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
                SemanticConflict(
                    symbol_id=symbol, reason="缺少函数语义注解"
                )
            )
        return (
            [annotations[symbol] for symbol in sorted(annotations)],
            conflicts,
        )

    def _file_annotations(
        self, annotations: list[FunctionAnnotation]
    ) -> list[FileAnnotation]:
        by_file: dict[str, list[FunctionAnnotation]] = defaultdict(list)
        for annotation in annotations:
            by_file[annotation.file_path].append(annotation)
        inputs = [(path, sorted(by_file[path], key=lambda item: item.symbol_id))
                  for path in sorted(by_file)]
        if self._max_concurrency == 1 or len(inputs) < 2:
            return [self._file_annotation(path, items) for path, items in inputs]
        futures = [
            global_heavy_pool.submit(self._file_annotation, path, items)
            for path, items in inputs
        ]
        return [future.result() for future in futures]

    def _file_annotation(
        self, file_path: str, items: list[FunctionAnnotation]
    ) -> FileAnnotation:
        expected_symbols = [item.symbol_id for item in items]
        DEBUG_MODEL = self._debug_model
        if DEBUG_MODEL:
            print(
                f"[DEBUG_MODEL][SEMANTIC][AGGREGATE][FILE] "
                f"file={file_path} functions={len(expected_symbols)}",
                flush=True,
            )
        draft = self._complete(
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
            response_model=FileAnnotationDraft,
        )
        return FileAnnotation(
            file_path=file_path,
            role=draft.role,
            function_symbols=expected_symbols,
            risks=draft.risks,
            confidence=draft.confidence,
        )

    def _project_annotation(
        self,
        analysis: AnalysisResult,
        files: list[FileAnnotation],
    ) -> ProjectAnnotation:
        expected_files = [item.file_path for item in files]
        DEBUG_MODEL = self._debug_model
        if DEBUG_MODEL:
            print(
                f"[DEBUG_MODEL][SEMANTIC][AGGREGATE][PROJECT] "
                f"files={len(expected_files)} entries={len(analysis.entry_points)} "
                f"cycles={len(analysis.cycles)} unresolved={len(analysis.unresolved_calls)}",
                flush=True,
            )
        draft = self._complete(
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
            response_model=ProjectAnnotationDraft,
        )
        if not draft.usage.strip():
            raise OrchestrationError("项目级语义缺少使用方法")
        return ProjectAnnotation(
            purpose=draft.purpose,
            usage=draft.usage,
            entry_points=analysis.entry_points,
            files=expected_files,
            risks=draft.risks,
            confidence=draft.confidence,
        )


# 兼容旧类名。
SemanticAnnotationAggregator = SemanticAggregator

__all__ = ["SemanticAggregator", "SemanticAnnotationAggregator"]
