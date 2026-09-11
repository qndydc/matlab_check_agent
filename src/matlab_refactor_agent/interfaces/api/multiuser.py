"""
Description: Provide employee sessions, uploaded projects, exports and runtime views.
References: FastAPI, SQLite, ZIP, and the process-wide executors.
Referenced By: Semantic and Migration Web applications.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import shutil
import sqlite3
import stat
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

from fastapi import APIRouter, Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from matlab_refactor_agent.domain.orchestration import new_job_id
from matlab_refactor_agent.orchestration.execution_pool import (
    global_heavy_pool,
    global_job_coordinator,
)


COOKIE_NAME = "matlab_atlas_employee"
EMPLOYEE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


@dataclass(frozen=True)
class WebRuntimeSettings:
    data_root: Path
    require_employee: bool
    administrators: frozenset[str]
    max_upload_bytes: int
    max_unpacked_bytes: int
    max_project_files: int
    user_quota_bytes: int

    @classmethod
    def from_environment(cls, database: Path) -> "WebRuntimeSettings":
        default_root = database.expanduser().resolve().parent
        root = Path(os.environ.get("MATLAB_DATA_ROOT", str(default_root))).expanduser().resolve()
        required = os.environ.get("MATLAB_REQUIRE_EMPLOYEE_ID", "0").strip().lower() in {
            "1", "true", "yes", "on",
        }
        admins = frozenset(
            item.strip() for item in os.environ.get("MATLAB_ADMIN_EMPLOYEE_IDS", "local").split(",")
            if item.strip()
        )
        return cls(
            data_root=root,
            require_employee=required,
            administrators=admins,
            max_upload_bytes=_int_env("MATLAB_MAX_UPLOAD_BYTES", 2 * 1024**3),
            max_unpacked_bytes=_int_env("MATLAB_MAX_UNPACKED_BYTES", 5 * 1024**3),
            max_project_files=_int_env("MATLAB_MAX_PROJECT_FILES", 100_000),
            user_quota_bytes=_int_env("MATLAB_USER_QUOTA_BYTES", 10 * 1024**3),
        )


@dataclass(frozen=True)
class ProjectRecord:
    project_id: str
    employee_id: str
    original_filename: str
    source_type: str
    storage_path: str
    upload_bytes: int
    unpacked_bytes: int | None
    sha256: str
    state: str
    created_at: str


class ControlStore:
    """Small shared SQLite catalogue for users, uploaded projects and job ownership."""

    def __init__(self, database: Path, runtime: WebRuntimeSettings | None = None) -> None:
        self.database = database.expanduser().resolve()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.runtime = runtime or WebRuntimeSettings.from_environment(self.database)
        self.runtime.data_root.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA busy_timeout = 30000")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.transaction() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users(
                    employee_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    last_login_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS projects(
                    project_id TEXT PRIMARY KEY,
                    employee_id TEXT NOT NULL,
                    original_filename TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    storage_path TEXT NOT NULL,
                    upload_bytes INTEGER NOT NULL,
                    unpacked_bytes INTEGER,
                    sha256 TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(employee_id) REFERENCES users(employee_id)
                );
                CREATE INDEX IF NOT EXISTS idx_projects_employee_created
                ON projects(employee_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS execution_jobs(
                    job_id TEXT PRIMARY KEY,
                    employee_id TEXT NOT NULL,
                    project_id TEXT,
                    job_type TEXT NOT NULL,
                    state TEXT NOT NULL,
                    stage TEXT,
                    settings_snapshot TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(employee_id) REFERENCES users(employee_id)
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_employee_updated
                ON execution_jobs(employee_id, updated_at DESC);
                """
            )
            connection.execute(
                """UPDATE execution_jobs SET state='interrupted',
                   error=COALESCE(error, '服务重启，任务已中断'), updated_at=?
                   WHERE state IN ('queued','running')""",
                (_now(),),
            )

    def touch_user(self, employee_id: str) -> None:
        now = _now()
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO users(employee_id, created_at, last_login_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(employee_id) DO UPDATE SET last_login_at=excluded.last_login_at""",
                (employee_id, now, now),
            )
        self.user_root(employee_id).mkdir(parents=True, exist_ok=True)

    def user_root(self, employee_id: str) -> Path:
        if not EMPLOYEE_PATTERN.fullmatch(employee_id):
            raise HTTPException(422, "工号只能包含字母、数字、点、下划线和连字符")
        root = (self.runtime.data_root / "users" / employee_id).resolve()
        try:
            root.relative_to(self.runtime.data_root)
        except ValueError as exc:
            raise HTTPException(403, "用户目录越界") from exc
        return root

    def project_root(self, employee_id: str, project_id: str) -> Path:
        root = (self.user_root(employee_id) / "projects" / project_id).resolve()
        try:
            root.relative_to(self.user_root(employee_id))
        except ValueError as exc:
            raise HTTPException(403, "项目目录越界") from exc
        return root

    def create_project(self, employee_id: str, filename: str) -> ProjectRecord:
        self.touch_user(employee_id)
        project_id = new_job_id()
        root = self.project_root(employee_id, project_id)
        root.mkdir(parents=True, exist_ok=False)
        record = ProjectRecord(
            project_id=project_id,
            employee_id=employee_id,
            original_filename=Path(filename or "project.zip").name,
            source_type="zip",
            storage_path=str(root / "source"),
            upload_bytes=0,
            unpacked_bytes=None,
            sha256="",
            state="uploading",
            created_at=_now(),
        )
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO projects(project_id, employee_id, original_filename,
                   source_type, storage_path, upload_bytes, unpacked_bytes, sha256,
                   state, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                tuple(asdict(record).values()),
            )
        return record

    def update_project(self, project_id: str, employee_id: str, **values: object) -> ProjectRecord:
        allowed = {"upload_bytes", "unpacked_bytes", "sha256", "state", "storage_path"}
        updates = {key: value for key, value in values.items() if key in allowed}
        if updates:
            assignments = ", ".join(f"{key}=?" for key in updates)
            with self.transaction() as connection:
                cursor = connection.execute(
                    f"UPDATE projects SET {assignments} WHERE project_id=? AND employee_id=?",
                    (*updates.values(), project_id, employee_id),
                )
                if cursor.rowcount == 0:
                    raise HTTPException(404, "项目不存在")
        return self.get_project(project_id, employee_id)

    def get_project(self, project_id: str, employee_id: str) -> ProjectRecord:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM projects WHERE project_id=? AND employee_id=?",
                (project_id, employee_id),
            ).fetchone()
        if row is None:
            raise HTTPException(404, "项目不存在")
        return ProjectRecord(**dict(row))

    def list_projects(self, employee_id: str) -> list[ProjectRecord]:
        with self.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM projects WHERE employee_id=? ORDER BY created_at DESC",
                (employee_id,),
            ).fetchall()
        return [ProjectRecord(**dict(row)) for row in rows]

    def delete_project(self, project_id: str, employee_id: str) -> None:
        record = self.get_project(project_id, employee_id)
        with self.transaction() as connection:
            running = connection.execute(
                """SELECT 1 FROM execution_jobs WHERE project_id=? AND employee_id=?
                   AND state IN ('queued','running') LIMIT 1""",
                (project_id, employee_id),
            ).fetchone()
            if running:
                raise HTTPException(409, "项目仍有关联任务正在运行")
            connection.execute(
                "DELETE FROM projects WHERE project_id=? AND employee_id=?",
                (project_id, employee_id),
            )
        root = Path(record.storage_path).resolve().parent
        expected = self.project_root(employee_id, project_id)
        if root == expected and root.is_dir():
            shutil.rmtree(root)

    def register_job(
        self, job_id: str, employee_id: str, job_type: str,
        *, project_id: str | None = None, state: str = "queued", stage: str | None = None,
    ) -> None:
        self.touch_user(employee_id)
        now = _now()
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO execution_jobs(job_id, employee_id, project_id, job_type,
                   state, stage, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(job_id) DO UPDATE SET state=excluded.state,
                   stage=excluded.stage, updated_at=excluded.updated_at""",
                (job_id, employee_id, project_id, job_type, state, stage, now, now),
            )

    def update_job(self, job_id: str, employee_id: str, **values: object) -> None:
        allowed = {"state", "stage", "settings_snapshot", "error", "project_id"}
        updates = {key: value for key, value in values.items() if key in allowed}
        if not updates:
            return
        updates["updated_at"] = _now()
        assignments = ", ".join(f"{key}=?" for key in updates)
        with self.transaction() as connection:
            connection.execute(
                f"UPDATE execution_jobs SET {assignments} WHERE job_id=? AND employee_id=?",
                (*updates.values(), job_id, employee_id),
            )

    def assert_job_owner(self, job_id: str, employee_id: str) -> None:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT employee_id FROM execution_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
        if row is not None and row["employee_id"] != employee_id:
            raise HTTPException(404, "任务不存在")

    def user_usage(self, employee_id: str) -> int:
        root = self.user_root(employee_id)
        return sum(path.stat().st_size for path in root.rglob("*") if path.is_file()) if root.exists() else 0

    def export_path(self, employee_id: str, job_id: str) -> Path:
        root = self.user_root(employee_id) / "exports"
        root.mkdir(parents=True, exist_ok=True)
        return root / f"{job_id}.zip"

    def storage_snapshot(self) -> dict[str, object]:
        usage = shutil.disk_usage(self.runtime.data_root)
        with self.transaction() as connection:
            users = connection.execute(
                """SELECT u.employee_id, COALESCE(SUM(p.upload_bytes + COALESCE(p.unpacked_bytes,0)),0) bytes
                   FROM users u LEFT JOIN projects p ON p.employee_id=u.employee_id
                   GROUP BY u.employee_id ORDER BY u.employee_id"""
            ).fetchall()
        return {
            "data_root": str(self.runtime.data_root),
            "disk_total": usage.total,
            "disk_used": usage.used,
            "disk_free": usage.free,
            "users": [dict(row) for row in users],
        }


class SessionRequest(BaseModel):
    employee_id: str = Field(min_length=1, max_length=64)


class EmployeeSession:
    def __init__(self, store: ControlStore) -> None:
        self.store = store
        secret_path = store.runtime.data_root / ".session-secret"
        if not secret_path.exists():
            temporary = secret_path.with_suffix(".part")
            temporary.write_text(secrets.token_hex(32), encoding="ascii")
            try:
                temporary.replace(secret_path)
            except OSError:
                temporary.unlink(missing_ok=True)
        self._secret = secret_path.read_bytes().strip()

    def _token(self, employee_id: str) -> str:
        signature = hmac.new(self._secret, employee_id.encode(), hashlib.sha256).digest()
        return employee_id + "." + base64.urlsafe_b64encode(signature).decode().rstrip("=")

    def _read(self, request: Request) -> str | None:
        token = request.cookies.get(COOKIE_NAME, "")
        if "." not in token:
            return None
        employee_id, supplied = token.rsplit(".", 1)
        if not EMPLOYEE_PATTERN.fullmatch(employee_id):
            return None
        return employee_id if hmac.compare_digest(supplied, self._token(employee_id).rsplit(".", 1)[1]) else None

    def current(self, request: Request) -> str:
        employee_id = self._read(request)
        if employee_id:
            return employee_id
        if not self.store.runtime.require_employee:
            self.store.touch_user("local")
            return "local"
        raise HTTPException(401, "请先输入工号")

    def administrator(self, request: Request) -> str:
        employee_id = self.current(request)
        if employee_id not in self.store.runtime.administrators:
            raise HTTPException(403, "只有管理员可以修改服务器设置")
        return employee_id

    def login(self, employee_id: str, response: Response) -> dict[str, object]:
        employee_id = employee_id.strip()
        if not EMPLOYEE_PATTERN.fullmatch(employee_id):
            raise HTTPException(422, "工号只能包含字母、数字、点、下划线和连字符")
        self.store.touch_user(employee_id)
        response.set_cookie(
            COOKIE_NAME,
            self._token(employee_id),
            httponly=True,
            samesite="lax",
            secure=os.environ.get("MATLAB_SECURE_COOKIE", "0").lower() in {"1", "true", "yes"},
            max_age=30 * 24 * 60 * 60,
            path="/",
        )
        return {"employee_id": employee_id, "administrator": employee_id in self.store.runtime.administrators}


def safe_extract_zip(archive: Path, destination: Path, runtime: WebRuntimeSettings) -> tuple[int, int]:
    total = 0
    files = 0
    seen: set[str] = set()
    destination.mkdir(parents=True, exist_ok=False)
    try:
        with zipfile.ZipFile(archive) as bundle:
            for info in bundle.infolist():
                name = info.filename.replace("\\", "/")
                if not name or "\x00" in name or any(ord(char) < 32 for char in name):
                    raise ValueError("ZIP 包含无效文件名")
                posix = PurePosixPath(name)
                windows = PureWindowsPath(name)
                if posix.is_absolute() or windows.is_absolute() or windows.drive or ".." in posix.parts:
                    raise ValueError("ZIP 包含越界路径")
                mode = info.external_attr >> 16
                file_type = stat.S_IFMT(mode)
                if stat.S_ISLNK(mode) or file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
                    raise ValueError("ZIP 不允许链接或特殊文件")
                if info.is_dir():
                    continue
                files += 1
                total += info.file_size
                if files > runtime.max_project_files:
                    raise ValueError("ZIP 文件数量超过服务器限制")
                if total > runtime.max_unpacked_bytes:
                    raise ValueError("ZIP 解压后大小超过服务器限制")
                target = (destination / Path(*posix.parts)).resolve()
                relative_key = target.as_posix().casefold()
                if relative_key in seen:
                    raise ValueError("ZIP 包含重复文件路径")
                seen.add(relative_key)
                try:
                    target.relative_to(destination.resolve())
                except ValueError as exc:
                    raise ValueError("ZIP 解压路径越界") from exc
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return files, total


def create_export(source: Path, target: Path) -> Path:
    if not source.is_dir():
        raise HTTPException(409, "任务尚未生成可下载文件")
    temporary = target.with_suffix(".zip.part")
    temporary.unlink(missing_ok=True)
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(source)
            if path.name in {".env", ".env.lock"} or path.suffix in {".db", ".sqlite", ".sqlite3"}:
                continue
            bundle.write(path, relative.as_posix())
    temporary.replace(target)
    return target


def install_identity_routes(api: FastAPI, session: EmployeeSession) -> None:
    router = APIRouter(prefix="/api/session")

    @router.post("")
    def login(payload: SessionRequest, response: Response) -> dict[str, object]:
        return session.login(payload.employee_id, response)

    @router.get("")
    def current(employee_id: str = Depends(session.current)) -> dict[str, object]:
        return {"employee_id": employee_id, "administrator": employee_id in session.store.runtime.administrators}

    @router.delete("", status_code=204)
    def logout(response: Response) -> None:
        response.delete_cookie(COOKIE_NAME, path="/")

    api.include_router(router)


def install_project_routes(api: FastAPI, session: EmployeeSession) -> None:
    store = session.store
    router = APIRouter(prefix="/api/source-projects")

    @api.post("/api/projects/upload", status_code=201, include_in_schema=False)
    @router.post("/upload", status_code=201)
    def upload(file: UploadFile = File(...), employee_id: str = Depends(session.current)) -> dict:
        filename = Path(file.filename or "").name
        if Path(filename).suffix.lower() != ".zip":
            raise HTTPException(415, "第一版只支持 ZIP 项目包")
        if store.user_usage(employee_id) >= store.runtime.user_quota_bytes:
            raise HTTPException(413, "用户存储配额已用完")
        record = store.create_project(employee_id, filename)
        root = store.project_root(employee_id, record.project_id)
        partial = root / "upload.zip.part"
        archive = root / "upload.zip"
        digest = hashlib.sha256()
        size = 0
        try:
            with partial.open("wb") as output:
                while chunk := file.file.read(1024 * 1024):
                    size += len(chunk)
                    if size > store.runtime.max_upload_bytes:
                        raise HTTPException(413, "上传文件超过服务器限制")
                    digest.update(chunk)
                    output.write(chunk)
            if store.user_usage(employee_id) > store.runtime.user_quota_bytes:
                raise HTTPException(413, "上传后将超过用户存储配额")
            partial.replace(archive)
            store.update_project(record.project_id, employee_id, state="extracting", upload_bytes=size, sha256=digest.hexdigest())
            _, unpacked = global_heavy_pool.run(safe_extract_zip, archive, root / "source", store.runtime)
            if store.user_usage(employee_id) > store.runtime.user_quota_bytes:
                shutil.rmtree(root / "source", ignore_errors=True)
                raise HTTPException(413, "解压后将超过用户存储配额")
            ready = store.update_project(record.project_id, employee_id, state="ready", unpacked_bytes=unpacked)
            return asdict(ready)
        except HTTPException:
            archive.unlink(missing_ok=True)
            shutil.rmtree(root / "source", ignore_errors=True)
            store.update_project(record.project_id, employee_id, state="failed", upload_bytes=size, sha256=digest.hexdigest())
            raise
        except (OSError, zipfile.BadZipFile, ValueError) as exc:
            archive.unlink(missing_ok=True)
            shutil.rmtree(root / "source", ignore_errors=True)
            store.update_project(record.project_id, employee_id, state="failed", upload_bytes=size, sha256=digest.hexdigest())
            raise HTTPException(422, f"项目包处理失败：{str(exc)[:200]}") from exc
        finally:
            file.file.close()
            partial.unlink(missing_ok=True)

    @router.get("")
    def projects(employee_id: str = Depends(session.current)) -> list[dict]:
        return [asdict(item) for item in store.list_projects(employee_id)]

    @router.get("/{project_id}")
    def project(project_id: str, employee_id: str = Depends(session.current)) -> dict:
        return asdict(store.get_project(project_id, employee_id))

    @router.delete("/{project_id}", status_code=204)
    def delete(project_id: str, employee_id: str = Depends(session.current)) -> None:
        store.delete_project(project_id, employee_id)

    api.include_router(router)


def install_admin_routes(api: FastAPI, session: EmployeeSession) -> None:
    router = APIRouter(prefix="/api/admin")

    @router.get("/runtime")
    def runtime(_employee: str = Depends(session.administrator)) -> dict[str, object]:
        return {**global_job_coordinator.snapshot(), **global_heavy_pool.snapshot()}

    @router.get("/jobs")
    def jobs(_employee: str = Depends(session.administrator)) -> list[dict]:
        with session.store.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM execution_jobs ORDER BY updated_at DESC LIMIT 500"
            ).fetchall()
        return [dict(row) for row in rows]

    @router.get("/storage")
    def storage(_employee: str = Depends(session.administrator)) -> dict[str, object]:
        return session.store.storage_snapshot()

    api.include_router(router)


__all__ = [
    "ControlStore", "EmployeeSession", "ProjectRecord", "WebRuntimeSettings",
    "create_export", "install_admin_routes", "install_identity_routes",
    "install_project_routes", "safe_extract_zip",
]
