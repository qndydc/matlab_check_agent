"""
Description: 验证三个确定性前处理 Worker 的独立 main 演示入口及结构化输出。
References: Scanner/Parser/Analyzer main、pytest、MATLAB fixture。
Referenced By: pytest 测试发现。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from matlab_refactor_agent.workers.analyzer_agent import main as analyzer_main
from matlab_refactor_agent.workers.parser_agent import main as parser_main
from matlab_refactor_agent.workers.scanner_agent import main as scanner_main


FIXTURE = Path(__file__).parents[2] / "fixtures" / "matlab_projects" / "basic"


def test_scanner_worker_main(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """作用：验证 Worker-1 独立入口；输入：MATLAB fixture；输出：成功结果断言；数据流：main -> manifest artifact -> JSON stdout。"""

    exit_code = scanner_main([str(FIXTURE), "--artifact-dir", str(tmp_path)])
    result = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert result["metrics"]["file_count"] == 7
    assert Path(result["artifacts"]["file_manifest"]).is_file()


def test_parser_and_analyzer_worker_mains(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """作用：验证 Worker-2/3 独立入口串联；输入：MATLAB fixture 和分片大小；输出：聚合及分析断言；数据流：Parser main -> scan artifact -> Analyzer main。"""

    parser_exit = parser_main(
        [
            str(FIXTURE),
            "--chunk-size",
            "2",
            "--artifact-dir",
            str(tmp_path),
        ]
    )
    parser_result = json.loads(capsys.readouterr().out)
    analyzer_exit = analyzer_main(
        [
            parser_result["artifacts"]["scan_result"],
            "--artifact-dir",
            str(tmp_path),
        ]
    )
    analyzer_result = json.loads(capsys.readouterr().out)
    assert parser_exit == 0
    assert parser_result["metrics"]["chunk_count"] == 4
    assert analyzer_exit == 0
    assert analyzer_result["metrics"]["node_count"] == 8
