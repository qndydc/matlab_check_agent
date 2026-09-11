"""
Description: 使用 SQLite 长期保存 Web 项目的任务状态、调用图和三级语义。
References: sqlite3、GraphDocument、SemanticIndex。
Referenced By: interfaces.api.app 和 Web API 持久化测试。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from matlab_refactor_agent.domain.semantics import SemanticIndex, SemanticProgressEvent
from matlab_refactor_agent.workers.graph_output import GraphDocument


@dataclass
class StoredWebProject:
    """作用：承载从 SQLite 恢复的一个完整 Web 项目快照。"""

    job_id: str
    project_path: Path
    state: str
    stage: str
    message: str
    error: str | None
    graph: GraphDocument | None
    semantics: SemanticIndex | None
    created_at: datetime
    updated_at: datetime
    employee_id: str = "local"
    project_id: str | None = None
    scan_reference: str | None = None
    analysis_reference: str | None = None
    source_fingerprint: str | None = None


class SQLiteWebProjectStore:
    """作用：提供 Web 项目快照的保存、恢复、列表和删除操作。"""

    def __init__(self, database: Path, *, recover_interrupted: bool = True) -> None:
        """作用：解析数据库路径、创建表并恢复异常中断任务。"""

        self.database = database.expanduser().resolve()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize(recover_interrupted=recover_interrupted)

    def save(self, project: StoredWebProject) -> None:
        """作用：原子新增或更新一个 Web 项目及其图和语义 JSON。"""

        graph_json = project.graph.model_dump_json() if project.graph else None
        semantics_json = (
            project.semantics.model_dump_json() if project.semantics else None
        )
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO web_projects(
                    job_id, project_path, state, stage, message, error,
                    graph_json, semantics_json, scan_reference, analysis_reference,
                    source_fingerprint, created_at, updated_at, employee_id, project_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    project_path=excluded.project_path,
                    state=excluded.state,
                    stage=excluded.stage,
                    message=excluded.message,
                    error=excluded.error,
                    graph_json=excluded.graph_json,
                    semantics_json=excluded.semantics_json,
                    scan_reference=excluded.scan_reference,
                    analysis_reference=excluded.analysis_reference,
                    source_fingerprint=excluded.source_fingerprint,
                    employee_id=excluded.employee_id,
                    project_id=excluded.project_id,
                    updated_at=excluded.updated_at
                """,
                (
                    project.job_id,
                    str(project.project_path),
                    project.state,
                    project.stage,
                    project.message,
                    project.error,
                    graph_json,
                    semantics_json,
                    project.scan_reference,
                    project.analysis_reference,
                    project.source_fingerprint,
                    project.created_at.isoformat(),
                    project.updated_at.isoformat(),
                    project.employee_id,
                    project.project_id,
                ),
            )

    def get(self, job_id: str, employee_id: str | None = None) -> StoredWebProject | None:
        """作用：按 Job ID 恢复一个 Web 项目快照。"""

        with self._transaction() as connection:
            if employee_id is None:
                row = connection.execute(
                    "SELECT * FROM web_projects WHERE job_id = ?", (job_id,)
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM web_projects WHERE job_id = ? AND employee_id = ?",
                    (job_id, employee_id),
                ).fetchone()
        return self._from_row(row) if row is not None else None

    def list(self, employee_id: str | None = None) -> list[StoredWebProject]:
        """作用：按最近更新时间倒序返回全部本地 Web 项目。"""

        with self._transaction() as connection:
            if employee_id is None:
                rows = connection.execute(
                    "SELECT * FROM web_projects ORDER BY updated_at DESC, job_id DESC"
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT * FROM web_projects WHERE employee_id = ?
                       ORDER BY updated_at DESC, job_id DESC""",
                    (employee_id,),
                ).fetchall()
        return [self._from_row(row) for row in rows]

    def analysis_snapshots(self, employee_id: str | None = None) -> list[StoredWebProject]:
        """返回可被迁移复用的静态分析，不把运行中的普通分析暴露为输入。"""

        with self._transaction() as connection:
            query = """
                SELECT * FROM web_projects
                WHERE graph_json IS NOT NULL
                  AND scan_reference IS NOT NULL
                  AND analysis_reference IS NOT NULL
                  AND source_fingerprint IS NOT NULL
                  AND NOT (stage = 'analyze' AND state IN ('queued', 'running'))
            """
            parameters: tuple[str, ...] = ()
            if employee_id is not None:
                query += " AND employee_id = ?"
                parameters = (employee_id,)
            query += " ORDER BY updated_at DESC, job_id DESC"
            rows = connection.execute(query, parameters).fetchall()
        return [self._from_row(row) for row in rows]

    def delete(self, job_id: str, employee_id: str | None = None) -> bool:
        """作用：删除指定 Web 项目的持久化图和三级语义副本。"""

        with self._transaction() as connection:
            if employee_id is None:
                cursor = connection.execute(
                    "DELETE FROM web_projects WHERE job_id = ?", (job_id,)
                )
            else:
                cursor = connection.execute(
                    "DELETE FROM web_projects WHERE job_id = ? AND employee_id = ?",
                    (job_id, employee_id),
                )
            if cursor.rowcount > 0:
                connection.execute(
                    "DELETE FROM semantic_progress_events WHERE job_id = ?", (job_id,)
                )
        return cursor.rowcount > 0

    def append_progress_event(
        self, job_id: str, event: SemanticProgressEvent
    ) -> None:
        """按序号持久化一个语义 LangGraph 事件，支持任务运行中增量读取。"""

        with self._transaction() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO semantic_progress_events(
                    job_id, sequence, event_json, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    job_id,
                    event.sequence,
                    event.model_dump_json(),
                    event.created_at.isoformat(),
                ),
            )

    def progress_events(
        self, job_id: str, *, after_sequence: int = 0
    ) -> list[SemanticProgressEvent]:
        """返回指定序号之后的事件，供轮询监控避免重复传输。"""

        with self._transaction() as connection:
            rows = connection.execute(
                """
                SELECT event_json
                FROM semantic_progress_events
                WHERE job_id = ? AND sequence > ?
                ORDER BY sequence
                """,
                (job_id, after_sequence),
            ).fetchall()
        return [
            SemanticProgressEvent.model_validate_json(row["event_json"])
            for row in rows
        ]

    def clear_progress_events(self, job_id: str) -> None:
        """在重新启动语义任务前清除同一 Web Job 的旧事件序列。"""

        with self._transaction() as connection:
            connection.execute(
                "DELETE FROM semantic_progress_events WHERE job_id = ?", (job_id,)
            )

    def _initialize(self, *, recover_interrupted: bool) -> None:
        """作用：初始化 SQLite 表，并把重启前未完成任务标记为失败。"""

        with self._transaction() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS web_projects(
                    job_id TEXT PRIMARY KEY,
                    project_path TEXT NOT NULL,
                    state TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    message TEXT NOT NULL,
                    error TEXT,
                    graph_json TEXT,
                    semantics_json TEXT,
                    scan_reference TEXT,
                    analysis_reference TEXT,
                    source_fingerprint TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS semantic_progress_events(
                    job_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(job_id, sequence)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_semantic_progress_job_sequence
                ON semantic_progress_events(job_id, sequence)
                """
            )
            existing = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(web_projects)")
            }
            for name in ("scan_reference", "analysis_reference", "source_fingerprint"):
                if name not in existing:
                    connection.execute(f"ALTER TABLE web_projects ADD COLUMN {name} TEXT")
            if "employee_id" not in existing:
                connection.execute(
                    "ALTER TABLE web_projects ADD COLUMN employee_id TEXT NOT NULL DEFAULT 'local'"
                )
            if "project_id" not in existing:
                connection.execute("ALTER TABLE web_projects ADD COLUMN project_id TEXT")
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_web_projects_employee_updated
                   ON web_projects(employee_id, updated_at DESC)"""
            )
            if recover_interrupted:
                connection.execute(
                    """
                    UPDATE web_projects
                    SET state = 'failed',
                        message = '服务重启，未完成任务已中断',
                        error = '任务执行期间服务被关闭'
                    WHERE state IN ('queued', 'running')
                    """
                )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> StoredWebProject:
        """作用：将 SQLite 行转换成带 Pydantic 图和语义模型的项目快照。"""

        return StoredWebProject(
            job_id=row["job_id"],
            project_path=Path(row["project_path"]),
            state=row["state"],
            stage=row["stage"],
            message=row["message"],
            error=row["error"],
            graph=(
                GraphDocument.model_validate_json(row["graph_json"])
                if row["graph_json"]
                else None
            ),
            semantics=(
                SemanticIndex.model_validate_json(row["semantics_json"])
                if row["semantics_json"]
                else None
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            employee_id=row["employee_id"] if "employee_id" in row.keys() else "local",
            project_id=row["project_id"] if "project_id" in row.keys() else None,
            scan_reference=row["scan_reference"],
            analysis_reference=row["analysis_reference"],
            source_fingerprint=row["source_fingerprint"],
        )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """作用：为每次操作创建短连接，并自动提交或回滚事务。"""

        connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
