"""
Description: 实现单个 WCC 的 Build Context、Reason、Act、Assemble 和 Observe 组件。
References: 迁移 Agent、CallChainContextBuilder、PythonProjectAssembler、CallChainObserver。
Referenced By: MatlabToPythonMigrationAgent 和迁移组件测试。
"""

from __future__ import annotations

import ast
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from matlab_refactor_agent.agents.base import AgentContext
from matlab_refactor_agent.agents.matlab_to_python.agent import (
    MatlabToPythonAgent,
)
from matlab_refactor_agent.agents.matlab_to_python.strategy import (
    ConversionReasonAgent,
)
from matlab_refactor_agent.domain.agents import AgentRequest
from matlab_refactor_agent.domain.diagnostics import DifferentialObservation, ValidationFact
from matlab_refactor_agent.domain.enums import AgentKind
from matlab_refactor_agent.domain.exceptions import OrchestrationError
from matlab_refactor_agent.domain.migration import (
    ActChunk,
    ActChunkPlan,
    ActContext,
    CallChainContext,
    ConversionStratagem,
    FrozenChunkInterface,
    GeneratedPythonFile,
    MigrationWorkUnit,
    TranslationResponse,
)
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore
from matlab_refactor_agent.infrastructure.llm import StructuredLLMClient
from matlab_refactor_agent.infrastructure.llm.token_budget import estimate_tokens
from matlab_refactor_agent.orchestration.call_lifecycle import CallObservationRecorder
from matlab_refactor_agent.orchestration.migration_state import MigrationStateStore
from matlab_refactor_agent.orchestration.execution_pool import global_heavy_pool
from matlab_refactor_agent.workers.call_chain_observer import CallChainObserver
from matlab_refactor_agent.workers.python_project_assembler import (
    PythonProjectAssembler,
)

from .context import CallChainContextBuilder
from .checkpoint import MigrationSession
from .chunking import ActChunkLimits, ActChunkPlanner, ActChunkStore
from .reason_policy import ReasonFallbackPolicy

_PROGRESS_LOCK = RLock()


class MigrationRuntime:
    """保存一次 Job 的共享依赖，并让五个图组件可以独立测试。"""

    def __init__(
        self,
        *,
        session: MigrationSession,
        artifacts: ArtifactStore,
        client: StructuredLLMClient,
        reason_client: StructuredLLMClient | None = None,
        reason_input_budget: int = 32_768,
        debug_model: bool = False,
        chunk_concurrency: int = 1,
    ) -> None:
        self.job_id = session.job_id
        self.current_chain_id: str | None = None
        self.plan = session.plan
        self.scan_reference = session.checkpoint.scan_reference
        self.analysis_reference = session.checkpoint.analysis_reference
        self.semantic_index_reference = (
            session.checkpoint.semantic_index_reference
        )
        self.store = artifacts
        self.artifacts: dict[str, str] = {}
        self._debug_model = debug_model
        self._chunk_concurrency = max(1, chunk_concurrency)
        self._runtime_lock = RLock()
        self._act_client = client
        self._call_recorder = CallObservationRecorder(artifacts, session.job_id)
        self._reason_agent = ConversionReasonAgent(
            reason_client or client, self._call_recorder, self._model_progress
        )
        self._reason_policy = ReasonFallbackPolicy(
            self._reason_agent, reason_input_budget
        )
        self._context_builder = CallChainContextBuilder(artifacts)
        self._units = {unit.unit_id: unit for unit in session.plan.units}
        # 从已有文件继续编号，续跑不能覆盖之前的尝试。
        self._attempts = {key: self._last_version("act-context", key) for key in self._units}
        self._context_versions = {key: self._last_version("context", key) for key in self._units}
        self._reason_versions = {key: self._last_version("stratagem", key) for key in self._units}
        self._reason_context_versions = {key: self._last_version("reason-context", key) for key in self._units}
        self._full_context_refs: dict[str, str] = {}
        self._adaptation_gaps: dict[str, list[str]] = {}
        self._heartbeat_started_at: str | None = None

    def _last_version(self, prefix: str, unit_id: str) -> int:
        paths = (self.store.root / self.job_id).glob(f"{prefix}-{unit_id}-*.json")
        return max((int(path.stem.rsplit("-", 1)[-1]) for path in paths
                    if path.stem.rsplit("-", 1)[-1].isdigit()), default=0)

    def record_step(self, stage: str, state: dict) -> None:
        """只记录阶段摘要，不把源码、提示词或模型全文写入终端。"""
        debug_only_stages = {
            "initialize", "select_call_chain", "project_validate", "publish",
        }
        if stage in debug_only_stages and not self._debug_model:
            return
        messages = {
            "initialize": "初始化迁移断点",
            "select_call_chain": "选择下一条 WCC 调用链",
            "build_context": "构建上下文", "reason": "规划转换策略",
            "act": "转换整条调用链", "assemble": "组装 Python 文件",
            "observe": "检查语法与 import", "freeze_chain": "保存已完成 WCC 断点",
            "manual_review": "需要人工复核",
            "project_validate": "执行项目级收尾检查",
            "publish": "汇总迁移产物",
        }
        chain_id = state.get("current_chain_id") or None
        self.current_chain_id = chain_id
        if stage == "manual_review" and chain_id is None:
            return
        message = messages.get(stage, stage)
        self._append_progress(stage, message, chain_id)
        DEBUG_MODEL = self._debug_model
        if DEBUG_MODEL:
            print(
                f"[DEBUG_MODEL][MIGRATION][{stage.upper()}][START] "
                f"job={self.job_id} chain={chain_id or '-'} message={message}",
                flush=True,
            )
        logging.getLogger(__name__).info("%s · %s", chain_id or self.job_id[:8], message)

    def _append_progress(
        self, stage: str, message: str, chain_id: str | None = None
    ) -> None:
        """将可读摘要写入 5174 已有的进度文件。"""

        path = self.store.root / self.job_id / "migration-progress.json"
        with _PROGRESS_LOCK:
            events = json.loads(self.store.read_text(str(path))) if path.is_file() else []
            event = {
                "sequence": events[-1]["sequence"] + 1 if events else 1,
                "time": datetime.now(timezone.utc).isoformat(),
                "stage": stage, "chain_id": chain_id, "message": message,
            }
            self.store.write_text(
                self.job_id, path.name,
                json.dumps([*events, event][-500:], ensure_ascii=False),
            )

    def _model_progress(self, event: dict[str, object]) -> None:
        """覆盖写入一条轻量心跳；不保存 reasoning/content 正文。"""

        now = datetime.now(timezone.utc).isoformat()
        if event.get("phase") == "started" or self._heartbeat_started_at is None:
            self._heartbeat_started_at = now
        payload = {
            "updated_at": now,
            "started_at": self._heartbeat_started_at,
            "chain_id": self.current_chain_id,
            **event,
        }
        elapsed_ms = int(payload.get("elapsed_ms") or 0)
        phase = str(payload.get("phase") or "running")
        tool = str(payload.get("tool") or "llm")
        payload["message"] = (
            f"{tool} · {phase} · {elapsed_ms / 1000:.1f}s · "
            f"reasoning {int(payload.get('reasoning_chars') or 0)} chars · "
            f"content {int(payload.get('content_chars') or 0)} chars"
        )
        with _PROGRESS_LOCK:
            self.store.write_text(
                self.job_id,
                "migration-heartbeat.json",
                json.dumps(payload, ensure_ascii=False),
            )
            if phase in {"completed", "failed"}:
                history_path = (
                    self.store.root / self.job_id / "migration-model-calls.json"
                )
                history = (
                    json.loads(self.store.read_text(str(history_path)))
                    if history_path.is_file() else []
                )
                self.store.write_text(
                    self.job_id,
                    history_path.name,
                    json.dumps([*history, payload][-200:], ensure_ascii=False),
                )
        DEBUG_MODEL = self._debug_model
        if DEBUG_MODEL:
            print(
                f"[DEBUG_MODEL][MIGRATION][HEARTBEAT] job={self.job_id} "
                f"chain={self.current_chain_id or '-'} {payload['message']} "
                f"first_token_ms={payload.get('first_token_ms')} "
                f"finish_reason={payload.get('finish_reason')}",
                flush=True,
            )

    def _debug(self, stage: str, message: str, unit_id: str | None = None) -> None:
        """仅在 DEBUG_MODEL 开启时同步输出终端和前端细节日志。"""

        DEBUG_MODEL = self._debug_model
        if DEBUG_MODEL:
            print(
                f"[DEBUG_MODEL][MIGRATION][{stage.upper()}][DETAIL] "
                f"job={self.job_id} chain={unit_id or '-'} {message}",
                flush=True,
            )
            self._append_progress(stage, message, unit_id)

    def build_context(
        self,
        unit_id: str,
        _observation_reference: str | None,
        stratagem_reference: str | None,
    ) -> str:
        requested = self._requested_context(stratagem_reference)
        self._context_versions[unit_id] += 1
        version = self._context_versions[unit_id]
        context = self._context_builder.build(
            scan_reference=self.scan_reference,
            analysis_reference=self.analysis_reference,
            semantic_reference=self.semantic_index_reference,
            plan=self.plan,
            unit=self._units[unit_id],
            requested_context=requested,
        )
        reference = self.store.write_model(
            self.job_id, f"context-{unit_id}-{version}.json", context
        )
        self.artifacts[f"context_{unit_id}_{version}"] = reference
        self._debug(
            "build_context",
            f"上下文 v{version} 已生成：WCC 函数 {len(context.functions)} 个，"
            f"依赖函数 {len(context.dependency_functions)} 个，"
            f"请求补充项 {len(requested)} 个，语义索引={context.semantic_index_used}",
            unit_id,
        )
        return reference

    def reason(
        self,
        unit_id: str,
        context_reference: str,
        observation_reference: str | None,
    ) -> str:
        context = self.store.read_model(
            self._full_context_refs.get(unit_id, context_reference), CallChainContext
        )
        observation = (
            self.store.read_model(
                observation_reference, DifferentialObservation
            )
            if observation_reference
            else None
        )
        def supplement(requested: list[str]) -> CallChainContext:
            expanded = self._context_builder.build(
                scan_reference=self.scan_reference, analysis_reference=self.analysis_reference,
                semantic_reference=self.semantic_index_reference, plan=self.plan,
                unit=self._units[unit_id], requested_context=requested,
            )
            self._context_versions[unit_id] += 1
            version = self._context_versions[unit_id]
            reference = self.store.write_model(self.job_id, f"context-{unit_id}-{version}.json", expanded)
            self._full_context_refs[unit_id] = reference
            self.artifacts[f"context_{unit_id}_{version}"] = reference
            self._debug(
                "build_context",
                f"Reason 请求补充上下文：{len(requested)} 项；"
                f"扩展后函数 {len(expanded.functions)} 个、依赖函数 "
                f"{len(expanded.dependency_functions)} 个",
                unit_id,
            )
            return expanded

        def record(view: CallChainContext) -> None:
            self._reason_context_versions[unit_id] += 1
            version = self._reason_context_versions[unit_id]
            self.artifacts[f"reason_context_{unit_id}_{version}"] = self.store.write_model(
                self.job_id, f"reason-context-{unit_id}-{version}.json", view)
            self._debug(
                "reason",
                f"送入模型的裁剪上下文 v{version} 已保存："
                f"函数 {len(view.functions)} 个、依赖函数 "
                f"{len(view.dependency_functions)} 个",
                unit_id,
            )

        stratagem = self._reason_policy.run(context, observation, supplement, record)
        if stratagem.action == "convert" and not stratagem.module_plan:
            stratagem.module_plan = {symbol: self.plan.symbol_to_module[symbol]
                                     for symbol in self._units[unit_id].symbol_ids
                                     if symbol in self.plan.symbol_to_module}
        self._reason_versions[unit_id] += 1
        attempt = self._reason_versions[unit_id]
        reference = self.store.write_model(
            self.job_id,
            f"stratagem-{unit_id}-{attempt}.json",
            stratagem,
        )
        self.artifacts[f"stratagem_{unit_id}_{attempt}"] = reference
        self._debug(
            "reason",
            f"策略 v{attempt}：action={stratagem.action}，"
            f"转换步骤 {len(stratagem.conversion_steps)} 项，"
            f"验证计划 {len(stratagem.validation_plan)} 项，"
            f"待补上下文 {len(stratagem.requested_context)} 项",
            unit_id,
        )
        return reference

    def act(
        self,
        unit_id: str,
        context_reference: str,
        stratagem_reference: str,
    ) -> str:
        self._attempts[unit_id] += 1
        context = self.store.read_model(
            self._full_context_refs.get(unit_id, context_reference), CallChainContext
        )
        stratagem = self.store.read_model(
            stratagem_reference, ConversionStratagem
        )
        planner = ActChunkPlanner()
        chunk_store = ActChunkStore(self.store, self.job_id, unit_id)
        chunk_plan_reference = chunk_store.initialize(
            planner.build(context, stratagem)
        )
        self.artifacts[f"act_chunks_{unit_id}"] = chunk_plan_reference
        self._prepare_failed_chunks(chunk_store, planner, context, stratagem)
        chunk_plan = chunk_store.load()
        self._debug(
            "act",
            f"ActChunk 计划：{len(ActChunkStore.leaves(chunk_plan))} 个有效块，"
            f"并发上限 {self._chunk_concurrency}；约束 input≤20K、"
            "output≤8K、functions≤6、modules≤4",
            unit_id,
        )
        self._run_chunks(chunk_store, planner, context, stratagem)
        translation = self._aggregate_chunks(unit_id, chunk_store.load())
        reference = self.store.write_model(
            self.job_id,
            f"translation-{unit_id}-chunks-{self._attempts[unit_id]}.json",
            translation,
        )
        self.store.write_text(
            self.job_id,
            f"act-attempt-{unit_id}-{self._attempts[unit_id]}.json",
            json.dumps({
                "wcc_id": unit_id,
                "chunk_plan_ref": chunk_plan_reference,
                "frozen_chunk_ids": [
                    item.chunk_id for item in ActChunkStore.leaves(
                        chunk_store.load()
                    ) if item.status == "frozen"
                ],
            }, ensure_ascii=False),
        )
        self._adaptation_gaps[unit_id] = [
            *stratagem.external_dependencies,
            *translation.manual_review,
        ]
        self._debug(
            "act",
            f"chunk 聚合完成：Python 文件 {len(translation.files)} 个，"
            f"人工复核项 {len(translation.manual_review)} 个，"
            f"置信度 {translation.confidence:.2f}",
            unit_id,
        )
        return reference

    def _run_chunks(
        self,
        chunk_store: ActChunkStore,
        planner: ActChunkPlanner,
        context: CallChainContext,
        stratagem: ConversionStratagem,
    ) -> None:
        while True:
            plan = chunk_store.load()
            leaves = ActChunkStore.leaves(plan)
            if leaves and all(item.status == "frozen" for item in leaves):
                return
            ready = ActChunkStore.ready(plan)
            if not ready:
                failed = [item for item in leaves if item.status == "failed"]
                detail = (
                    f"{failed[0].chunk_id} 未完成"
                    if failed else "chunk 依赖图无法继续调度"
                )
                raise OrchestrationError(f"ActChunk 执行失败: {detail}")
            wave = ready[:self._chunk_concurrency]
            self._append_progress(
                "act_chunk_wave",
                f"执行 chunk wave：{', '.join(item.chunk_id for item in wave)}",
                context.unit.unit_id,
            )
            futures = [
                global_heavy_pool.submit(
                    self._run_chunk,
                    chunk_store,
                    planner,
                    chunk,
                    context,
                    stratagem,
                )
                for chunk in wave
            ]
            errors: list[BaseException] = []
            for future in futures:
                try:
                    future.result()
                except BaseException as exc:
                    errors.append(exc)
            if errors:
                failed = [
                    item for item in ActChunkStore.leaves(chunk_store.load())
                    if item.status == "failed"
                ]
                terminal = [item for item in failed if len(item.symbol_ids) == 1]
                if terminal:
                    raise OrchestrationError(
                        f"单函数 ActChunk 仍失败: {terminal[0].chunk_id}"
                    ) from errors[0]

    def _run_chunk(
        self,
        chunk_store: ActChunkStore,
        planner: ActChunkPlanner,
        chunk: ActChunk,
        context: CallChainContext,
        stratagem: ConversionStratagem,
    ) -> None:
        current = chunk_store.update(
            chunk.chunk_id,
            status="running",
            attempts=chunk.attempts + 1,
            last_error=None,
        )
        try:
            dependency_interfaces = [
                self.store.read_model(reference, FrozenChunkInterface)
                for reference in (
                    next(
                        item.interface_ref for item in chunk_store.load().chunks
                        if item.chunk_id == dependency
                    )
                    for dependency in current.depends_on_chunks
                )
                if reference
            ]
            act_context = planner.context_for(
                current, context, stratagem, dependency_interfaces
            )
            actual_tokens = estimate_tokens(act_context.model_dump_json(indent=2))
            if (
                actual_tokens > ActChunkLimits().input_tokens
                and len(current.symbol_ids) > 1
            ):
                raise OrchestrationError(
                    f"chunk 实际输入 {actual_tokens} tokens 超过 20K"
                )
            act_context_reference = self.store.write_model(
                self.job_id,
                f"act-context-{current.chunk_id}-{current.attempts}.json",
                act_context,
            )
            self._append_progress(
                "act_chunk",
                f"转换 {current.chunk_id}：函数 {current.function_count}、"
                f"模块 {current.module_count}、input≈{actual_tokens}、"
                f"output≈{current.output_tokens}",
                context.unit.unit_id,
            )
            act_agent = MatlabToPythonAgent(
                self._act_client,
                self._call_recorder,
                lambda event: self._model_progress({
                    **event, "chunk_id": current.chunk_id
                }),
            )
            result = act_agent.run(
                AgentRequest(
                    job_id=self.job_id,
                    agent_kind=AgentKind.MATLAB_TO_PYTHON,
                    artifact_refs={"act_context": act_context_reference},
                ),
                AgentContext(self.store),
            )
            if not result.success or not result.proposals:
                raise OrchestrationError(
                    f"MATLAB 到 Python chunk 转换失败: {current.chunk_id}"
                )
            translation_reference = str(
                result.proposals[0].payload["translation_ref"]
            )
            translation = self.store.read_model(
                translation_reference, TranslationResponse
            )
            interface = self._chunk_interface(current, translation)
            interface_reference = self.store.write_model(
                self.job_id,
                f"act-interface-{current.chunk_id}.json",
                interface,
            )
            chunk_store.update(
                current.chunk_id,
                status="frozen",
                translation_ref=translation_reference,
                interface_ref=interface_reference,
            )
            with self._runtime_lock:
                self.artifacts.update(result.artifacts)
                self.artifacts[f"act_interface_{current.chunk_id}"] = (
                    interface_reference
                )
            self._append_progress(
                "freeze_chunk",
                f"已冻结 {current.chunk_id} 接口",
                context.unit.unit_id,
            )
        except BaseException as exc:
            chunk_store.fail(current.chunk_id, exc)
            if len(current.symbol_ids) > 1:
                chunk_store.split(
                    planner, current.chunk_id, context, stratagem
                )
                self._append_progress(
                    "split_chunk",
                    f"{current.chunk_id} 失败，已二分并保留其他冻结 chunk",
                    context.unit.unit_id,
                )
                return
            self._append_progress(
                "failed_chunk",
                f"单函数 chunk {current.chunk_id} 失败",
                context.unit.unit_id,
            )
            raise

    def _prepare_failed_chunks(
        self,
        chunk_store: ActChunkStore,
        planner: ActChunkPlanner,
        context: CallChainContext,
        stratagem: ConversionStratagem,
    ) -> None:
        """续跑只重置失败叶子；多函数失败叶子先二分。"""

        for chunk in ActChunkStore.leaves(chunk_store.load()):
            if chunk.status == "running":
                chunk_store.fail(chunk.chunk_id, RuntimeError("进程在 chunk 执行中中断"))
                chunk = next(
                    item for item in chunk_store.load().chunks
                    if item.chunk_id == chunk.chunk_id
                )
            if chunk.status != "failed":
                continue
            if len(chunk.symbol_ids) > 1:
                chunk_store.split(planner, chunk.chunk_id, context, stratagem)
            else:
                chunk_store.update(chunk.chunk_id, status="pending")

    def _aggregate_chunks(
        self, unit_id: str, plan: ActChunkPlan
    ) -> TranslationResponse:
        files: dict[str, GeneratedPythonFile] = {}
        assumptions: list[str] = []
        manual_review: list[str] = []
        confidences: list[float] = []
        for chunk in ActChunkStore.leaves(plan):
            if chunk.status != "frozen" or not chunk.translation_ref:
                raise OrchestrationError(
                    f"ActChunk 尚未冻结，不能聚合: {chunk.chunk_id}"
                )
            response = self.store.read_model(
                chunk.translation_ref, TranslationResponse
            )
            assumptions.extend(response.assumptions)
            manual_review.extend(response.manual_review)
            confidences.append(response.confidence)
            for generated in response.files:
                existing = files.get(generated.path)
                if existing is None:
                    files[generated.path] = generated
                    continue
                content = existing.content
                if generated.content.strip() != existing.content.strip():
                    content = self._merge_python_content(
                        existing.content, generated.content
                    )
                files[generated.path] = GeneratedPythonFile(
                    path=generated.path,
                    content=content,
                    symbol_ids=list(dict.fromkeys([
                        *existing.symbol_ids, *generated.symbol_ids
                    ])),
                )
        response = TranslationResponse(
            unit_id=unit_id,
            files=list(files.values()),
            assumptions=list(dict.fromkeys(assumptions)),
            manual_review=list(dict.fromkeys(manual_review)),
            confidence=min(confidences, default=0.0),
        )
        covered = [
            symbol for item in response.files for symbol in item.symbol_ids
        ]
        if set(covered) != set(self._units[unit_id].symbol_ids):
            raise OrchestrationError("chunk 聚合结果未完整覆盖 WCC")
        return response

    @staticmethod
    def _merge_python_content(first: str, second: str) -> str:
        """合并同模块 chunk，优先生成语法稳定且 import 位于顶部的文件。"""

        try:
            trees = [ast.parse(first), ast.parse(second)]
        except SyntaxError:
            return first.rstrip() + "\n\n" + second.lstrip()
        future_imports: list[ast.stmt] = []
        imports: list[ast.stmt] = []
        body: list[ast.stmt] = []
        seen_imports: set[str] = set()
        seen_definitions: set[tuple[type, str]] = set()
        for tree in trees:
            for node in tree.body:
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.module == "__future__"
                ):
                    key = ast.dump(node, include_attributes=False)
                    if key not in seen_imports:
                        future_imports.append(node)
                        seen_imports.add(key)
                    continue
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    key = ast.dump(node, include_attributes=False)
                    if key not in seen_imports:
                        imports.append(node)
                        seen_imports.add(key)
                    continue
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    key = (type(node), node.name)
                    if key in seen_definitions:
                        continue
                    seen_definitions.add(key)
                body.append(node)
        merged = ast.fix_missing_locations(ast.Module(
            body=[*future_imports, *imports, *body], type_ignores=[]
        ))
        return ast.unparse(merged).rstrip() + "\n"

    @staticmethod
    def _chunk_interface(
        chunk: ActChunk, translation: TranslationResponse
    ) -> FrozenChunkInterface:
        declarations: list[str] = []
        for generated in translation.files:
            try:
                tree = ast.parse(generated.content)
            except SyntaxError:
                continue
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    declarations.append(
                        f"def {node.name}({ast.unparse(node.args)})"
                    )
                elif isinstance(node, ast.ClassDef):
                    declarations.append(f"class {node.name}")
        return FrozenChunkInterface(
            chunk_id=chunk.chunk_id,
            symbol_ids=chunk.symbol_ids,
            modules=sorted({item.path for item in translation.files}),
            declarations=declarations,
            summary=(
                f"已转换 {len(chunk.symbol_ids)} 个符号；"
                f"输出 {len(translation.files)} 个 Python 文件"
            ),
        )

    def assemble(self, unit_id: str, translation_reference: str) -> str:
        translation = self.store.read_model(
            translation_reference, TranslationResponse
        )
        assembler = PythonProjectAssembler(
            self.store.root
            / self.job_id
            / "generated-python"
            / unit_id
            / f"attempt-{self._attempts[unit_id]}"
        )
        paths = assembler.assemble(translation)
        reference = self.store.write_text(
            self.job_id,
            f"assembly-{unit_id}-{self._attempts[unit_id]}.json",
            json.dumps([str(path) for path in paths], ensure_ascii=False),
        )
        self._debug(
            "assemble",
            f"已写入隔离输出目录，共 {len(paths)} 个 Python 文件",
            unit_id,
        )
        return reference

    def observe(self, unit_id: str, assembly_reference: str) -> str:
        paths = [
            Path(item)
            for item in json.loads(self.store.read_text(assembly_reference))
        ]
        observation = CallChainObserver().observe(
            self._units[unit_id], paths
        )
        if self._adaptation_gaps.get(unit_id):
            observation.facts.append(ValidationFact(kind="manual_review", passed=True,
                actual=self._adaptation_gaps[unit_id],
                detail="模型声明的适配与复核项；保留到项目级报告，不阻断 WCC"))
        self._mark_observation_failures(unit_id, observation)
        passed = sum(1 for fact in observation.facts if fact.passed)
        failed = len(observation.facts) - passed
        self._debug(
            "observe",
            f"检查完成：overall={'PASS' if observation.passed else 'FAIL'}，"
            f"通过 {passed} 项，失败 {failed} 项；"
            + "，".join(
                f"{fact.kind}={'pass' if fact.passed else 'fail'}"
                for fact in observation.facts
            ),
            unit_id,
        )
        return self.store.write_model(
            self.job_id, f"observation-{unit_id}-{self._attempts[unit_id]}.json", observation
        )

    def project_validate(self, migration_state_reference: str) -> str:
        """统一检查当前生成文件并汇总各 WCC 的人工复核项。"""

        states = MigrationStateStore(
            self.store, self.job_id, migration_state_reference
        ).load()
        paths: list[Path] = []
        review_items: list[dict[str, object]] = []
        for state in states:
            if state.assembly_ref:
                paths.extend(
                    Path(item)
                    for item in json.loads(self.store.read_text(state.assembly_ref))
                )
            if state.observation_ref:
                chain_observation = self.store.read_model(
                    state.observation_ref, DifferentialObservation
                )
                items = [
                    item
                    for fact in chain_observation.facts
                    if fact.kind == "manual_review"
                    for item in (
                        fact.actual if isinstance(fact.actual, list)
                        else [fact.actual]
                    )
                ]
                if items:
                    review_items.append({
                        "unit_id": state.unit_id,
                        "items": items,
                    })
        observation = CallChainObserver().observe(
            MigrationWorkUnit(unit_id="project", symbol_ids=["project"]), paths
        )
        if review_items:
            observation.facts.append(ValidationFact(
                kind="manual_review", passed=True, actual=review_items,
                detail="各 WCC 的人工复核项统一汇总；不作为发布阻断条件",
            ))
        reference = self.store.write_model(
            self.job_id, "project-observation.json", observation
        )
        self.artifacts["project_observation"] = reference
        self._debug(
            "project_validate",
            f"项目检查完成：overall={'PASS' if observation.passed else 'FAIL'}，"
            f"Python 文件 {len(paths)} 个，人工复核 WCC {len(review_items)} 条",
        )
        return reference

    def _mark_observation_failures(
        self, unit_id: str, observation: DifferentialObservation
    ) -> None:
        """把 WCC 检查失败映射回产出相关文件的叶子 chunk。"""

        failed_facts = [
            fact for fact in observation.facts
            if not fact.passed and fact.kind != "dependency_adaptation"
        ]
        chunk_store = ActChunkStore(self.store, self.job_id, unit_id)
        if not failed_facts or not chunk_store.path.is_file():
            return
        failed_paths = {
            str(fact.actual).replace("\\", "/")
            for fact in failed_facts
            if fact.kind == "syntax" and fact.actual
        }
        leaves = [
            item for item in ActChunkStore.leaves(chunk_store.load())
            if item.status == "frozen" and item.translation_ref
        ]
        affected: list[ActChunk] = []
        if failed_paths:
            for chunk in leaves:
                translation = self.store.read_model(
                    str(chunk.translation_ref), TranslationResponse
                )
                if any(
                    path.endswith(generated.path.replace("\\", "/"))
                    for path in failed_paths for generated in translation.files
                ):
                    affected.append(chunk)
        # 项目级 import、运行或差分问题不能可靠归属单个 chunk；保留已生成断点。
        if not affected:
            return
        for chunk in affected:
            chunk_store.update(
                chunk.chunk_id,
                status="failed",
                last_error="WCC Observation 定位到该 chunk",
            )
        self._append_progress(
            "locate_failed_chunk",
            "WCC Observation 失败定位到："
            + ", ".join(item.chunk_id for item in affected),
            unit_id,
        )

    def _requested_context(
        self, stratagem_reference: str | None
    ) -> list[str]:
        if not stratagem_reference:
            return []
        return self.store.read_model(
            stratagem_reference, ConversionStratagem
        ).requested_context


__all__ = ["MigrationRuntime"]
