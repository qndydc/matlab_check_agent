"""
Description: 实现 CLI 各业务命令，入口 main.py 只负责解析和分发。
References: AnalysisService、MigrationService、渲染与图导出组件。
Referenced By: interfaces.cli.main 和 CLI 集成测试。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable

from pydantic import BaseModel
from rich.console import Console

from matlab_refactor_agent.application import AnalysisService, MigrationService
from matlab_refactor_agent.domain.exceptions import OrchestrationError
from matlab_refactor_agent.infrastructure.config import AppSettings
from matlab_refactor_agent.orchestration import SQLiteStateManager
from matlab_refactor_agent.workers.graph_output import (
    render_dependency_tree,
    write_graph_json,
    write_mermaid,
)

from .render import (
    render_analysis,
    render_job_status,
    render_migration,
    render_scan,
    render_semantic_index,
)

CommandHandler = Callable[[argparse.Namespace, AppSettings, Console], int]


def dispatch(
    args: argparse.Namespace,
    settings: AppSettings,
    console: Console,
) -> int:
    """把已解析参数交给对应命令，不在入口中包含业务流程。"""

    handlers: dict[str, CommandHandler] = {
        "scan": _scan,
        "analyze": _analyze,
        "annotate": _annotate,
        "migrate": _migrate,
        "status": _status,
    }
    return handlers[args.command](args, settings, console)


def _scan(
    args: argparse.Namespace, settings: AppSettings, console: Console
) -> int:
    service = AnalysisService(settings)
    result = service.scan(_project(args, settings))
    _output(result, args.json, console, render_scan)
    _render_job_id(service.last_job_id, console)
    return 0


def _analyze(
    args: argparse.Namespace, settings: AppSettings, console: Console
) -> int:
    project = _project(args, settings)
    service = AnalysisService(settings)
    result = service.analyze(project)
    _output(result, args.json, console, render_analysis)
    if args.tree:
        render_dependency_tree(result, console)
    graph_json_path = args.graph_json
    mermaid_path = args.mermaid
    if settings.io.auto_export_graphs and service.last_job_id:
        graph_dir = settings.io.graph_dir.expanduser().resolve()
        graph_json_path = graph_json_path or (
            graph_dir / f"{service.last_job_id}.graph.json"
        )
        mermaid_path = mermaid_path or (
            graph_dir / f"{service.last_job_id}.mermaid.mmd"
        )
    if graph_json_path is not None:
        _assert_output_outside_project(graph_json_path, project)
        write_graph_json(result, graph_json_path)
        console.print(f"图数据已写入：{graph_json_path}")
    if mermaid_path is not None:
        _assert_output_outside_project(mermaid_path, project)
        write_mermaid(result, mermaid_path)
        console.print(f"Mermaid 图已写入：{mermaid_path}")
    _render_job_id(service.last_job_id, console)
    return 0


def _annotate(
    args: argparse.Namespace, settings: AppSettings, console: Console
) -> int:
    service = AnalysisService(settings)
    try:
        result = (service.resume_annotation(args.resume, project_root=args.project)
                  if args.resume else service.annotate(_project(args, settings)))
    except (Exception, KeyboardInterrupt):
        _render_job_id(service.last_job_id, console)
        raise
    _output(result, args.json, console, render_semantic_index)
    _render_job_id(service.last_job_id, console)
    return 0


def _migrate(
    args: argparse.Namespace, settings: AppSettings, console: Console
) -> int:
    if args.resume and args.semantic_index is not None:
        raise OrchestrationError(
            "恢复任务沿用原 SemanticIndex，不能同时传入 --semantic-index"
        )
    service = MigrationService(settings)
    try:
        if args.resume:
            result = service.resume_translation_artifacts(
                args.resume, project_root=args.project
            )
        else:
            result = service.generate_translation_artifacts(
                _project(args, settings),
                semantic_index_reference=_semantic_reference(args),
            )
    except (Exception, KeyboardInterrupt):
        _render_resume_hint(args, service, console)
        raise
    _output(result, args.json, console, render_migration)
    _render_job_id(service.last_job_id, console)
    return 0


def _status(
    args: argparse.Namespace, settings: AppSettings, console: Console
) -> int:
    state = SQLiteStateManager(settings.orchestrator.state_db)
    job = state.get_job(args.job_id)
    if job is None:
        raise OrchestrationError(f"Job 不存在: {args.job_id}")
    render_job_status(job, state.task_statuses(args.job_id), console)
    return 0


def _project(args: argparse.Namespace, settings: AppSettings) -> Path:
    return args.project or settings.io.input_path


def _semantic_reference(args: argparse.Namespace) -> str | None:
    if args.semantic_index is None:
        return None
    path = args.semantic_index.expanduser().resolve()
    if not path.is_file():
        raise OrchestrationError(f"SemanticIndex artifact 不存在: {path}")
    return str(path)


def _output(
    result: BaseModel,
    destination: str | None,
    console: Console,
    renderer: Callable[[object, Console], None],
) -> None:
    if destination is None:
        renderer(result, console)
    else:
        _write_json(result, destination)


def _write_json(model: BaseModel, destination: str) -> None:
    payload = json.dumps(
        model.model_dump(mode="json"), ensure_ascii=False, indent=2
    )
    if destination == "-":
        sys.stdout.write(payload + "\n")
        return
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload + "\n", encoding="utf-8")


def _render_job_id(job_id: str | None, console: Console) -> None:
    if job_id is not None:
        console.print(f"Job ID：{job_id}")


def _render_resume_hint(
    args: argparse.Namespace,
    service: MigrationService,
    console: Console,
) -> None:
    if (
        args.resume
        or service.last_job_id is None
        or service.last_checkpoint_reference is None
    ):
        return
    console.print(
        "可从已完成 WCC 后继续："
        f"matlab-refactor migrate --resume {service.last_job_id}"
    )


def _assert_output_outside_project(destination: Path, project: Path) -> None:
    target = destination.expanduser().resolve()
    root = project.expanduser().resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return
    raise OrchestrationError(f"输出文件不能位于输入项目内: {target}")


__all__ = ["dispatch"]
