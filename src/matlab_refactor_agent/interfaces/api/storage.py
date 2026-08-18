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

from matlab_refactor_agent.domain.semantics import SemanticIndex
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


class SQLiteWebProjectStore:
    """作用：提供 Web 项目快照的保存、恢复、列表和删除操作。"""

    def __init__(self, database: Path) -> None:
        """作用：解析数据库路径、创建表并恢复异常中断任务。"""

        self.database = database.expanduser().resolve()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

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
                    graph_json, semantics_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    project_path=excluded.project_path,
                    state=excluded.state,
                    stage=excluded.stage,
                    message=excluded.message,
                    error=excluded.error,
                    graph_json=excluded.graph_json,
                    semantics_json=excluded.semantics_json,
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
                    project.created_at.isoformat(),
                    project.updated_at.isoformat(),
                ),
            )

    def get(self, job_id: str) -> StoredWebProject | None:
        """作用：按 Job ID 恢复一个 Web 项目快照。"""

        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM web_projects WHERE job_id = ?", (job_id,)
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def list(self) -> list[StoredWebProject]:
        """作用：按最近更新时间倒序返回全部本地 Web 项目。"""

        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM web_projects ORDER BY updated_at DESC, job_id DESC"
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def delete(self, job_id: str) -> bool:
        """作用：删除指定 Web 项目的持久化图和三级语义副本。"""

        with self._transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM web_projects WHERE job_id = ?", (job_id,)
            )
        return cursor.rowcount > 0

    def _initialize(self) -> None:
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
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
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
