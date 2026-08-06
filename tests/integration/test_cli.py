"""
Description: 验证 CLI 分析、完整 JSON 和可视化文件导出链路。
References: interfaces.cli.main、MATLAB basic fixture、pytest。
Referenced By: pytest 测试发现。
"""

import json
from pathlib import Path

import pytest

from matlab_refactor_agent.interfaces.cli.main import main


FIXTURE = Path(__file__).parents[1] / "fixtures" / "matlab_projects" / "basic"


@pytest.mark.skipif(
    pytest.importorskip("maxx", reason="集成测试需要 maxx") is None,
    reason="集成测试需要 maxx",
)
def test_analyze_writes_json(tmp_path: Path) -> None:
    """作用：验证 CLI JSON 集成链路；输入：fixture 和输出路径；输出：断言结果；数据流：CLI -> 扫描/分析 -> JSON 文件。"""

    output = tmp_path / "analysis.json"

    exit_code = main(["analyze", str(FIXTURE), "--json", str(output)])

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert ["cycleA", "cycleB"] in payload["cycles"]
    assert "orphan" in payload["orphans"]
    assert payload["dependencies"]
    assert not any("ignored" in item["file_path"] for item in payload["functions"])


def test_analyze_exports_visualization_files(tmp_path: Path) -> None:
    """作用：验证 CLI 可视化导出；输入：fixture 和输出路径；输出：断言结果；数据流：CLI 参数 -> 分析 -> Graph JSON/Mermaid。"""

    graph_path = tmp_path / "graph.json"
    mermaid_path = tmp_path / "graph.mmd"

    exit_code = main(
        [
            "analyze",
            str(FIXTURE),
            "--graph-json",
            str(graph_path),
            "--mermaid",
            str(mermaid_path),
        ]
    )

    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert graph["schema_version"] == "1.0"
    assert graph["nodes"]
    assert graph["edges"]
    assert mermaid_path.read_text(encoding="utf-8").startswith("flowchart LR")
