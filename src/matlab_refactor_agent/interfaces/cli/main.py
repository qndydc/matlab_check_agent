"""
Description: 定义 CLI 参数并把命令分发给独立业务处理器。
References: argparse、cli.commands、应用配置。
Referenced By: __main__、console script 和 CLI 集成测试。
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from rich.console import Console

from matlab_refactor_agent.domain.exceptions import MatlabRefactorError
from matlab_refactor_agent.infrastructure.config import load_settings
from matlab_refactor_agent.infrastructure.logging import configure_logging

from .commands import dispatch


def build_parser() -> argparse.ArgumentParser:
    """定义稳定 CLI 语法，不执行任何业务流程。"""

    parser = argparse.ArgumentParser(
        prog="matlab-refactor",
        description="扫描、理解并迁移大型 MATLAB 代码库。",
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
        "--tree", action="store_true", help="在终端显示函数调用/依赖树"
    )
    analyze.add_argument(
        "--graph-json",
        type=Path,
        metavar="FILE",
        help="导出供 Web 图组件使用的版本化节点/边 JSON",
    )
    analyze.add_argument(
        "--mermaid", type=Path, metavar="FILE", help="导出 Mermaid flowchart 文件"
    )

    annotate = subparsers.add_parser(
        "annotate", help="生成函数、文件和项目三级语义注解"
    )
    _add_project_argument(annotate)
    _add_json_argument(annotate)
    annotate.add_argument("--resume", metavar="JOB_ID", help="续跑语义断点；不传则完全重跑并重建结构图")

    migrate = subparsers.add_parser(
        "migrate", help="以 WCC 为单位运行 MATLAB 到 Python 迁移 Agent"
    )
    _add_project_argument(migrate)
    _add_json_argument(migrate)
    migrate.add_argument(
        "--semantic-index",
        type=Path,
        metavar="FILE",
        help="可选的 SemanticIndex artifact JSON；省略时只使用结构上下文",
    )
    migrate.add_argument(
        "--resume",
        metavar="JOB_ID",
        help="恢复同一迁移 Job，跳过已经完成并冻结的 WCC",
    )

    status = subparsers.add_parser(
        "status", help="查询 Orchestrator Job 和 Worker 状态"
    )
    status.add_argument("job_id", help="Job ID")
    return parser


def _add_project_argument(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "project",
        type=Path,
        nargs="?",
        help="MATLAB 项目目录；省略时读取 MATLAB_REFACTOR_INPUT_PATH",
    )


def _add_json_argument(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--json",
        nargs="?",
        const="-",
        metavar="FILE",
        help="输出完整结果 JSON；省略 FILE 时写入标准输出",
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 总入口：解析参数、加载配置、分发命令和统一错误码。"""

    args = build_parser().parse_args(argv)
    console = Console(stderr=getattr(args, "json", None) == "-")
    try:
        settings = load_settings()
        configure_logging(settings.logging.level)
        return dispatch(args, settings, console)
    except MatlabRefactorError as exc:
        console.print(f"[bold red]错误：[/bold red]{exc}")
        return 2
    except KeyboardInterrupt:
        console.print("\n操作已取消。")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
