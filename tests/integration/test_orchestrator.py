"""
Description: 验证 Scanner/Parser/Analyzer 流水线、SQLite 状态和 status CLI。
References: Orchestrator、SQLiteStateManager、AppSettings、MATLAB fixture。
Referenced By: pytest 测试发现。
"""

from pathlib import Path

from matlab_refactor_agent.infrastructure.config import AppSettings
from matlab_refactor_agent.interfaces.cli.main import main
from matlab_refactor_agent.orchestration import Orchestrator, SQLiteStateManager


FIXTURE = Path(__file__).parents[1] / "fixtures" / "matlab_projects" / "basic"


def test_orchestrator_persists_worker_pipeline(tmp_path: Path) -> None:
    """作用：验证多 Worker 分析流水线；输入：MATLAB fixture 和临时状态目录；输出：断言结果；数据流：Orchestrator -> Scanner/Parser/Analyzer -> SQLite/artifacts。"""

    database = tmp_path / "state.db"
    artifact_dir = tmp_path / "jobs"
    settings = AppSettings(
        orchestrator={
            "state_db": database,
            "checkpoint_db": tmp_path / "checkpoints.db",
            "artifact_dir": artifact_dir,
            "parser_chunk_size": 3,
        }
    )

    outcome = Orchestrator.from_settings(settings).run_analysis(FIXTURE)

    state = SQLiteStateManager(database)
    job = state.get_job(outcome.job_id)
    statuses = state.task_statuses(outcome.job_id)
    assert job is not None
    assert str(job.status) == "completed"
    assert [kind for _, kind, _ in statuses] == [
        "scanner",
        "parser",
        "parser",
        "parser",
        "parser",
        "analyzer",
    ]
    assert all(status == "completed" for _, _, status in statuses)
    assert Path(outcome.artifacts["file_manifest"]).is_file()
    assert len(
        [key for key in outcome.artifacts if key.startswith("parse_chunk_")]
    ) == 3
    assert Path(outcome.artifacts["scan_result"]).is_file()
    assert Path(outcome.artifacts["analysis_result"]).is_file()
    assert outcome.result.dependencies
    assert ["cycleA", "cycleB"] in outcome.result.cycles


def test_orchestrator_scan_runs_scanner_parser_fanout_and_aggregate(
    tmp_path: Path,
) -> None:
    """作用：验证扫描解析 fan-out/fan-in；输入：MATLAB fixture 和 chunk size；输出：任务图断言；数据流：Scanner -> 三个 Parser chunk -> Parser aggregate。"""

    database = tmp_path / "state.db"
    settings = AppSettings(
        orchestrator={
            "state_db": database,
            "checkpoint_db": tmp_path / "checkpoints.db",
            "artifact_dir": tmp_path / "jobs",
            "parser_chunk_size": 3,
        }
    )

    outcome = Orchestrator.from_settings(settings).run_scan(FIXTURE)

    statuses = SQLiteStateManager(database).task_statuses(outcome.job_id)
    assert [kind for _, kind, _ in statuses] == [
        "scanner",
        "parser",
        "parser",
        "parser",
        "parser",
    ]
    assert all(status == "completed" for _, _, status in statuses)
    assert len(outcome.result.files) == 7


def test_status_command_reads_persisted_job(tmp_path: Path, monkeypatch) -> None:
    """作用：验证状态查询 CLI；输入：已完成 Job 和临时环境配置；输出：退出码断言；数据流：status 参数 -> SQLiteStateManager -> 终端。"""

    database = tmp_path / "state.db"
    artifact_dir = tmp_path / "jobs"
    settings = AppSettings(
        orchestrator={
            "state_db": database,
            "checkpoint_db": tmp_path / "checkpoints.db",
            "artifact_dir": artifact_dir,
        }
    )
    outcome = Orchestrator.from_settings(settings).run_scan(FIXTURE)
    monkeypatch.setenv("MATLAB_REFACTOR_STATE_DB", str(database))
    monkeypatch.setenv("MATLAB_REFACTOR_ARTIFACT_DIR", str(artifact_dir))

    exit_code = main(["status", outcome.job_id])

    assert exit_code == 0


def test_empty_project_still_runs_parser_aggregate(tmp_path: Path) -> None:
    """作用：验证零文件 fan-in 边界；输入：空项目目录；输出：空 ScanResult 和任务状态；数据流：空 manifest -> 无 chunk -> Parser aggregate。"""

    project = tmp_path / "empty-project"
    project.mkdir()
    database = tmp_path / "state.db"
    settings = AppSettings(
        orchestrator={
            "state_db": database,
            "checkpoint_db": tmp_path / "checkpoints.db",
            "artifact_dir": tmp_path / "jobs",
            "parser_chunk_size": 2,
        }
    )

    outcome = Orchestrator.from_settings(settings).run_scan(project)

    statuses = SQLiteStateManager(database).task_statuses(outcome.job_id)
    assert outcome.result.files == []
    assert [kind for _, kind, _ in statuses] == ["scanner", "parser"]
    assert all(status == "completed" for _, _, status in statuses)
