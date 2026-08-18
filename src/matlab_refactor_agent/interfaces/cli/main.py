"""
Description: 定义 scan、analyze、annotate、plan、review、report、status 命令及结构化导出流程。
References: AnalysisService、SQLiteStateManager、Visualizer、argparse。
Referenced By: __main__、console script 和 CLI 集成测试。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from pydantic import BaseModel
from rich.console import Console

from matlab_refactor_agent.application import AnalysisService
from matlab_refactor_agent.workers.graph_output import (
    render_dependency_tree,
    write_graph_json,
    write_mermaid,
)
from matlab_refactor_agent.domain.exceptions import MatlabRefactorError
from matlab_refactor_agent.domain.exceptions import OrchestrationError
from matlab_refactor_agent.domain.planning import ReviewDecision
from matlab_refactor_agent.infrastructure.config import load_settings
from matlab_refactor_agent.infrastructure.logging import configure_logging
from matlab_refactor_agent.orchestration import SQLiteStateManager

from .render import (
    render_analysis,
    render_job_status,
    render_natural_language_report,
    render_refactor_review,
    render_scan,
    render_semantic_index,
)


def build_parser() -> argparse.ArgumentParser:
    """作用：构建命令行语法；输入：无；输出：ArgumentParser；数据流：命令定义 -> argparse 解析器。"""

    parser = argparse.ArgumentParser(
        prog="matlab-refactor",
        description="扫描并分析大型 MATLAB 代码库。",
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="扫描 .m 文件并提取函数元数据")
    _add_project_argument(scan)
    _add_json_argument(scan)

    analyze = subparsers.add_parser("analyze", help="构建调用图并分析依赖关系")
    _add_project_argument(analyze)
    _add_json_argument(analyze)
    analyze.add_argument(
        "--tree",
        action="store_true",
        help="在终端显示函数调用/依赖树",
    )
    analyze.add_argument(
        "--graph-json",
        type=Path,
        metavar="FILE",
        help="导出供 Web 图组件使用的版本化节点/边 JSON",
    )
    analyze.add_argument(
        "--mermaid",
        type=Path,
        metavar="FILE",
        help="导出 Mermaid flowchart 文件",
    )
    annotate = subparsers.add_parser(
        "annotate",
        help="生成三级语义注解及模块职责、命名目录候选",
    )
    _add_project_argument(annotate)
    _add_json_argument(annotate)
    plan = subparsers.add_parser(
        "plan", help="生成重构计划并暂停等待人工审查"
    )
    _add_project_argument(plan)
    _add_json_argument(plan)
    review = subparsers.add_parser(
        "review", help="查看、批准或拒绝待审重构计划"
    )
    review.add_argument("job_id", help="待审 Job ID")
    review.add_argument(
        "--decision",
        choices=["approve", "reject", "request_changes"],
        help="省略时只查看计划",
    )
    review.add_argument("--comment", default="", help="人工审查意见")
    _add_json_argument(review)
    report = subparsers.add_parser(
        "report", help="查询验证结束后生成的自然语言重构报告"
    )
    report.add_argument("job_id", help="已验证或验证失败的 Job ID")
    _add_json_argument(report)
    status = subparsers.add_parser("status", help="查询 Orchestrator Job 和 Worker 状态")
    status.add_argument("job_id", help="Job ID")
    return parser


def _add_project_argument(command: argparse.ArgumentParser) -> None:
    """作用：添加可由 `.env` 提供的项目路径；输入：子命令；输出：可选位置参数。"""

    command.add_argument(
        "project",
        type=Path,
        nargs="?",
        help="MATLAB 项目目录；省略时读取 MATLAB_REFACTOR_INPUT_PATH",
    )


def _add_json_argument(command: argparse.ArgumentParser) -> None:
    """作用：添加通用结果 JSON 参数；输入：子命令解析器；输出：无；数据流：参数定义 -> scan/analyze CLI。"""

    command.add_argument(
        "--json",
        nargs="?",
        const="-",
        metavar="FILE",
        help="输出完整结果 JSON；省略 FILE 时写入标准输出",
    )


def main(argv: Sequence[str] | None = None) -> int:
    """作用：执行 CLI；输入：可选参数序列；输出：进程退出码；数据流：参数 -> 配置/服务 -> 终端或 JSON。"""

    args = build_parser().parse_args(argv)
    console = Console(stderr=getattr(args, "json", None) == "-")
    try:
        settings = load_settings()
        configure_logging(settings.logging.level)
        if args.command == "status":
            state = SQLiteStateManager(settings.orchestrator.state_db)
            job = state.get_job(args.job_id)
            if job is None:
                raise OrchestrationError(f"Job 不存在: {args.job_id}")
            render_job_status(job, state.task_statuses(args.job_id), console)
            return 0
        service = AnalysisService(settings)
        project = getattr(args, "project", None) or settings.io.input_path
        if args.command == "report":
            result = service.report(args.job_id)
            if args.json is not None:
                _write_json(result, args.json)
            else:
                render_natural_language_report(result, console)
        elif args.command == "review":
            decision = (
                ReviewDecision(action=args.decision, comment=args.comment)
                if args.decision
                else None
            )
            result = service.review(args.job_id, decision)
            if args.json is not None:
                _write_json(result, args.json)
            else:
                render_refactor_review(result, console)
        elif args.command == "scan":
            result = service.scan(project)
            if args.json is not None:
                _write_json(result, args.json)
            else:
                render_scan(result, console)
        elif args.command == "analyze":
            result = service.analyze(project)
            if args.json is not None:
                _write_json(result, args.json)
            else:
                render_analysis(result, console)
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
        elif args.command == "annotate":
            result = service.annotate(project)
            if args.json is not None:
                _write_json(result, args.json)
            else:
                render_semantic_index(result, console)
        else:
            result = service.plan(project)
            if args.json is not None:
                _write_json(result, args.json)
            else:
                render_refactor_review(result, console)
        if service.last_job_id is not None:
            console.print(f"Job ID：{service.last_job_id}")
        return 0
    except MatlabRefactorError as exc:
        console.print(f"[bold red]错误：[/bold red]{exc}")
        return 2
    except KeyboardInterrupt:
        console.print("\n操作已取消。")
        return 130


def _write_json(model: BaseModel, destination: str) -> None:
    """作用：输出结构化结果；输入：Pydantic 模型和目标；输出：无；数据流：领域模型 -> UTF-8 JSON -> 标准输出或文件。"""

    payload = json.dumps(model.model_dump(mode="json"), ensure_ascii=False, indent=2)
    if destination == "-":
        sys.stdout.write(payload + "\n")
        return
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload + "\n", encoding="utf-8")


def _assert_output_outside_project(destination: Path, project: Path) -> None:
    """作用：阻止图文件写入输入项目；输入：目标文件与项目目录；输出：无或只读边界异常。"""

    target = destination.expanduser().resolve()
    root = project.expanduser().resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return
    raise OrchestrationError(f"输出文件不能位于输入项目内: {target}")


if __name__ == "__main__":
    raise SystemExit(main())
