"""
Description: 验证 CLI 分析、图导出和状态查询。
References: CLI、SQLiteStateManager、MATLAB fixture。
Referenced By: pytest 测试发现。
"""

import json
from pathlib import Path

from matlab_refactor_agent.domain.orchestration import JobRecord
from matlab_refactor_agent.interfaces.cli.main import main
from matlab_refactor_agent.orchestration import SQLiteStateManager

FIXTURE = Path(__file__).parents[1] / "fixtures" / "matlab_projects" / "basic"


def test_analyze_writes_json_and_graphs(tmp_path: Path) -> None:
    output = tmp_path / "analysis.json"
    graph = tmp_path / "graph.json"
    mermaid = tmp_path / "graph.mmd"

    exit_code = main(["analyze", str(FIXTURE), "--json", str(output),
                      "--graph-json", str(graph), "--mermaid", str(mermaid)])

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["functions"] and payload["dependencies"]
    assert json.loads(graph.read_text(encoding="utf-8"))["nodes"]
    assert mermaid.read_text(encoding="utf-8").startswith("flowchart LR")


def test_status_reads_persisted_job(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "state.db"
    state = SQLiteStateManager(database)
    state.save_job(JobRecord(job_id="job123", project_root="D:/source"))
    monkeypatch.setenv("MATLAB_REFACTOR_STATE_DB", str(database))

    assert main(["status", "job123"]) == 0
