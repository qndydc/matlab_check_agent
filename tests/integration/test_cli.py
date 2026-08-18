"""
Description: 验证 CLI 分析、完整 JSON 和可视化文件导出链路。
References: interfaces.cli.main、MATLAB basic fixture、pytest。
Referenced By: pytest 测试发现。
"""

import json
from pathlib import Path

import pytest

from matlab_refactor_agent.domain.reporting import (
    NaturalLanguageReport,
    NaturalLanguageReportOutcome,
    ReportCheckNarrative,
)
from matlab_refactor_agent.domain.orchestration import JobRecord
from matlab_refactor_agent.orchestration import SQLiteStateManager
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


def test_analyze_uses_dotenv_default_input_and_graph_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """作用：验证省略项目参数时从 env 输入并自动导出图；输入：临时 `.env`；输出：分析与图文件。"""

    env_file = tmp_path / ".env"
    env_file.write_text(
        f"MATLAB_REFACTOR_INPUT_PATH={FIXTURE.as_posix()}\n"
        f"MATLAB_REFACTOR_STATE_DB={(tmp_path / 'state.db').as_posix()}\n"
        f"MATLAB_REFACTOR_CHECKPOINT_DB={(tmp_path / 'checkpoint.db').as_posix()}\n"
        f"MATLAB_REFACTOR_ARTIFACT_DIR={(tmp_path / 'jobs').as_posix()}\n"
        f"MATLAB_REFACTOR_GRAPH_OUTPUT_DIR={(tmp_path / 'graphs').as_posix()}\n"
        "MATLAB_REFACTOR_AUTO_EXPORT_GRAPHS=true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MATLAB_REFACTOR_ENV_FILE", str(env_file))
    output = tmp_path / "analysis.json"

    exit_code = main(["analyze", "--json", str(output)])

    assert exit_code == 0
    assert json.loads(output.read_text(encoding="utf-8"))["functions"]
    assert len(list((tmp_path / "graphs").glob("*.graph.json"))) == 1
    assert len(list((tmp_path / "graphs").glob("*.mermaid.mmd"))) == 1


def test_report_command_routes_and_writes_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """作用：验证 report CLI 路由和 JSON 契约；输入：假应用服务与 Job ID；输出：报告文件。"""

    report = NaturalLanguageReport(
        job_id="job123",
        attempt=0,
        outcome="validated",
        source_root="D:/source",
        output_root="D:/output",
        project_purpose="处理测试数据",
        copied_file_count=3,
        changed_matlab_files=["main.m"],
        source_tree_unchanged=True,
        validation_passed=True,
        title="交付报告",
        executive_summary="隔离重构和验证均已完成。",
        validation_summary="阻断检查通过。",
        check_findings=[
            ReportCheckNarrative(
                check_id="source_immutability",
                status="passed",
                interpretation="源项目哈希保持不变。",
            )
        ],
    )
    outcome = NaturalLanguageReportOutcome(
        job_id="job123",
        report=report,
        report_ref=str(tmp_path / "report.json"),
        markdown_ref=str(tmp_path / "report.md"),
    )

    class FakeService:
        def __init__(self, settings) -> None:
            self.last_job_id = None

        def report(self, job_id: str) -> NaturalLanguageReportOutcome:
            assert job_id == "job123"
            self.last_job_id = job_id
            return outcome

    monkeypatch.setattr(
        "matlab_refactor_agent.interfaces.cli.main.AnalysisService",
        FakeService,
    )
    output = tmp_path / "report-output.json"

    exit_code = main(["report", "job123", "--json", str(output)])

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["job_id"] == "job123"
    assert payload["report"]["outcome"] == "validated"
    assert payload["markdown_ref"].endswith("report.md")


def test_agent_cli_commands_route_to_application_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """作用：验证 annotate/plan/review 的后端入口分发；输入：三条 CLI；输出：调用序列与 JSON。"""

    calls: list[tuple[str, str]] = []
    result = NaturalLanguageReport(
        job_id="job123",
        attempt=0,
        outcome="validated",
        source_root="D:/source",
        output_root="D:/output",
        project_purpose="测试",
        copied_file_count=0,
        source_tree_unchanged=True,
        validation_passed=True,
        title="测试结果",
        executive_summary="测试入口已调用。",
        validation_summary="测试。",
    )

    class FakeService:
        def __init__(self, settings) -> None:
            self.last_job_id = "job123"

        def annotate(self, project: Path) -> NaturalLanguageReport:
            calls.append(("annotate", str(project)))
            return result

        def plan(self, project: Path) -> NaturalLanguageReport:
            calls.append(("plan", str(project)))
            return result

        def review(self, job_id: str, decision) -> NaturalLanguageReport:
            calls.append(("review", f"{job_id}:{decision.action}"))
            return result

    monkeypatch.setattr(
        "matlab_refactor_agent.interfaces.cli.main.AnalysisService",
        FakeService,
    )
    commands = [
        ["annotate", str(FIXTURE), "--json", str(tmp_path / "annotate.json")],
        ["plan", str(FIXTURE), "--json", str(tmp_path / "plan.json")],
        [
            "review",
            "job123",
            "--decision",
            "approve",
            "--json",
            str(tmp_path / "review.json"),
        ],
    ]

    assert [main(command) for command in commands] == [0, 0, 0]
    assert [item[0] for item in calls] == ["annotate", "plan", "review"]
    assert calls[-1][1] == "job123:approve"


def test_status_command_reads_persisted_job(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """作用：验证 status CLI 读取真实 SQLite；输入：持久化 Job 和配置；输出：终端状态。"""

    state_db = tmp_path / "state.db"
    state = SQLiteStateManager(state_db)
    state.save_job(JobRecord(job_id="job123", project_root="D:/source"))
    monkeypatch.setenv("MATLAB_REFACTOR_STATE_DB", str(state_db))
    monkeypatch.setenv(
        "MATLAB_REFACTOR_CHECKPOINT_DB", str(tmp_path / "checkpoints.db")
    )
    monkeypatch.setenv("MATLAB_REFACTOR_ARTIFACT_DIR", str(tmp_path / "jobs"))

    exit_code = main(["status", "job123"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "job123" in output
    assert "created" in output
