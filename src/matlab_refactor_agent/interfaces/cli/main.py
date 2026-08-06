"""
Description: 定义 scan、analyze、status 命令及结构化文件导出流程。
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
from matlab_refactor_agent.capabilities.visualizer import (
    render_dependency_tree,
    write_graph_json,
    write_mermaid,
)
from matlab_refactor_agent.domain.exceptions import MatlabRefactorError
from matlab_refactor_agent.domain.exceptions import OrchestrationError
from matlab_refactor_agent.infrastructure.config import load_settings
from matlab_refactor_agent.infrastructure.logging import configure_logging
from matlab_refactor_agent.orchestration import SQLiteStateManager

from .render import render_analysis, render_job_status, render_scan


def build_parser() -> argparse.ArgumentParser:
    """作用：构建命令行语法；输入：无；输出：ArgumentParser；数据流：命令定义 -> argparse 解析器。"""

    parser = argparse.ArgumentParser(
        prog="matlab-refactor",
        description="扫描并分析大型 MATLAB 代码库。",
    )
    parser.add_argument("--config", type=Path, help="YAML 配置文件")
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="扫描 .m 文件并提取函数元数据")
    scan.add_argument("project", type=Path, help="MATLAB 项目目录")
    _add_json_argument(scan)

    analyze = subparsers.add_parser("analyze", help="构建调用图并分析依赖关系")
    analyze.add_argument("project", type=Path, help="MATLAB 项目目录")
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
    status = subparsers.add_parser("status", help="查询 Orchestrator Job 和 Worker 状态")
    status.add_argument("job_id", help="Job ID")
    return parser


def _add_json_argument(command: argparse.ArgumentParser) -> None:
    """作用：添加通用结果 JSON 参数；输入：子命令解析器；输出：无；数据流：参数定义 -> scan/analyze CLI。"""

    command.add_argument(
        "--json",
        nargs="?",
        const="-",
        metavar="FILE",
        help="输出完整分析 JSON；省略 FILE 时写入标准输出",
    )


def main(argv: Sequence[str] | None = None) -> int:
    """作用：执行 CLI；输入：可选参数序列；输出：进程退出码；数据流：参数 -> 配置/服务 -> 终端或 JSON。"""

    args = build_parser().parse_args(argv)
    console = Console(stderr=getattr(args, "json", None) == "-")
    try:
        settings = load_settings(args.config)
        configure_logging(settings.logging.level)
        if args.command == "status":
            state = SQLiteStateManager(settings.orchestrator.state_db)
            job = state.get_job(args.job_id)
            if job is None:
                raise OrchestrationError(f"Job 不存在: {args.job_id}")
            render_job_status(job, state.task_statuses(args.job_id), console)
            return 0
        service = AnalysisService(settings)
        if args.command == "scan":
            result = service.scan(args.project)
            if args.json is not None:
                _write_json(result, args.json)
            else:
                render_scan(result, console)
        else:
            result = service.analyze(args.project)
            if args.json is not None:
                _write_json(result, args.json)
            else:
                render_analysis(result, console)
            if args.tree:
                render_dependency_tree(result, console)
            if args.graph_json is not None:
                write_graph_json(result, args.graph_json)
                console.print(f"图数据已写入：{args.graph_json}")
            if args.mermaid is not None:
                write_mermaid(result, args.mermaid)
                console.print(f"Mermaid 图已写入：{args.mermaid}")
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


if __name__ == "__main__":
    raise SystemExit(main())
