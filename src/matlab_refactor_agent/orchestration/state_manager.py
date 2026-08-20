"""
Description: 使用 SQLite/WAL 持久化 Job、Task、payload 和 WorkerResult。
References: sqlite3、domain.orchestration、domain.enums。
Referenced By: Orchestrator、status CLI 和集成测试。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from matlab_refactor_agent.domain.enums import JobStatus, TaskStatus
from matlab_refactor_agent.domain.orchestration import (
    JobRecord,
    TaskEnvelope,
    WorkerResult,
    utc_now,
)


class SQLiteStateManager:
    """作用：持久化 Job、Task 和 Worker 结果；输入：状态模型；输出：可恢复 SQLite 记录；数据流：Orchestrator 事件 -> SQLite -> 状态查询。"""

    def __init__(self, database: Path) -> None:
        """作用：初始化 SQLite 状态库；输入：数据库路径；输出：StateManager；数据流：配置路径 -> schema 创建。"""

        self.database = database.expanduser().resolve()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def save_job(self, job: JobRecord) -> None:
        """作用：新增或更新 Job；输入：JobRecord；输出：无；数据流：领域状态 -> UPSERT jobs。"""

        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO jobs(job_id, project_root, status, error, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    project_root=excluded.project_root,
                    status=excluded.status,
                    error=excluded.error,
                    updated_at=excluded.updated_at
                """,
                (
                    job.job_id,
                    job.project_root,
                    str(job.status),
                    job.error,
                    job.created_at.isoformat(),
                    job.updated_at.isoformat(),
                ),
            )

    def set_job_status(
        self, job: JobRecord, status: JobStatus, error: str | None = None
    ) -> JobRecord:
        """作用：迁移 Job 状态；输入：Job、目标状态和错误；输出：更新记录；数据流：调度事件 -> model_copy -> SQLite。"""

        updated = job.model_copy(
            update={"status": status, "error": error, "updated_at": utc_now()}
        )
        self.save_job(updated)
        return updated

    def save_task(
        self, task: TaskEnvelope, result: WorkerResult | None = None
    ) -> None:
        """作用：新增或更新 Task；输入：TaskEnvelope 和可选结果；输出：无；数据流：任务状态/结果 -> UPSERT tasks。"""

        result_json = (
            json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
            if result is not None
            else None
        )
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO tasks(
                    task_id, job_id, worker_kind, status, priority,
                    payload_json, depends_on_json, result_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status=excluded.status,
                    payload_json=excluded.payload_json,
                    result_json=COALESCE(excluded.result_json, tasks.result_json),
                    updated_at=excluded.updated_at
                """,
                (
                    task.task_id,
                    task.job_id,
                    str(task.worker_kind),
                    str(task.status),
                    task.priority,
                    json.dumps(task.payload, ensure_ascii=False),
                    json.dumps(task.depends_on, ensure_ascii=False),
                    result_json,
                    task.created_at.isoformat(),
                    task.updated_at.isoformat(),
                ),
            )

    def set_task_status(
        self,
        task: TaskEnvelope,
        status: TaskStatus,
        result: WorkerResult | None = None,
    ) -> TaskEnvelope:
        """作用：迁移 Task 状态；输入：任务、目标状态和结果；输出：更新任务；数据流：执行事件 -> model_copy -> SQLite。"""

        updated = task.model_copy(update={"status": status, "updated_at": utc_now()})
        self.save_task(updated, result)
        return updated

    def get_job(self, job_id: str) -> JobRecord | None:
        """作用：查询 Job；输入：Job ID；输出：JobRecord 或空；数据流：SQLite row -> 领域模型。"""

        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return JobRecord.model_validate(dict(row)) if row is not None else None

    def jobs_for_project(self, project_root: str) -> list[JobRecord]:
        """作用：按最近更新时间查询项目 Job；输入：规范化项目路径；输出：候选 checkpoint 所属 Job。"""

        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs WHERE project_root = ? "
                "ORDER BY updated_at DESC, created_at DESC",
                (project_root,),
            ).fetchall()
        return [JobRecord.model_validate(dict(row)) for row in rows]

    def task_statuses(self, job_id: str) -> list[tuple[str, str, str]]:
        """作用：查询 Job 下任务状态；输入：Job ID；输出：任务/Worker/状态元组；数据流：SQLite rows -> 状态报告。"""

        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT task_id, worker_kind, status FROM tasks WHERE job_id = ? "
                "ORDER BY created_at, task_id",
                (job_id,),
            ).fetchall()
        return [(row["task_id"], row["worker_kind"], row["status"]) for row in rows]

    def _initialize(self) -> None:
        """作用：创建状态库 schema；输入：数据库连接；输出：表和索引；数据流：DDL -> SQLite。"""

        with self._transaction() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs(
                    job_id TEXT PRIMARY KEY,
                    project_root TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks(
                    task_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    worker_kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    depends_on_json TEXT NOT NULL,
                    result_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(job_id)
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_job ON tasks(job_id);
                CREATE INDEX IF NOT EXISTS idx_jobs_project_updated
                    ON jobs(project_root, updated_at DESC);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        """作用：创建配置一致的 SQLite 连接；输入：数据库路径；输出：Connection；数据流：路径 -> row_factory/foreign_keys。"""

        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """作用：管理 SQLite 事务和连接关闭；输入：数据库配置；输出：连接迭代器；数据流：connect -> commit/rollback -> close。"""

        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()
