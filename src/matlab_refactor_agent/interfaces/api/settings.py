"""
Description: 两个 Web 应用共享的模型设置 API、安全密钥更新和 .env 原子持久化。
References: LLMSettings、load_settings、FastAPI、dotenv。
Referenced By: Semantic/Migration API 和共享 ModelSettings 前端。
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
from io import StringIO
import os
from pathlib import Path
import re
import tempfile
from threading import RLock
import time

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from dotenv.parser import parse_stream
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from matlab_refactor_agent.domain.exceptions import ConfigurationError
from matlab_refactor_agent.infrastructure.config import (
    LLMSettings, OrchestratorSettings, _ENV_FIELDS, load_settings,
)

# 不开放目录、数据库或任意环境变量的写入。
EDITABLE_FIELDS = {
    field: env for env, (section, field) in _ENV_FIELDS.items()
    if (section == "llm" and field != "api_key_env")
    or (section == "orchestrator" and field == "max_workers")
}
_ENV_LOCK = RLock()


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: dict[str, object] = Field(default_factory=dict)
    api_key: SecretStr | None = None
    clear_api_key: bool = False
    revision: str | None = None


class LegacyModelUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1, max_length=200)


def env_file_path(configured: Path | None = None) -> Path:
    return (configured or Path(os.environ.get("MATLAB_REFACTOR_ENV_FILE", ".env"))).expanduser().resolve()


@contextmanager
def _locked_env(path: Path):
    """线程锁 + 操作系统文件锁：两个后端同时保存时不会丢失彼此的修改。"""
    with _ENV_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.with_name(path.name + ".lock").open("a+b") as lock:
            if lock.tell() == 0:
                lock.write(b"0")
                lock.flush()
            deadline = time.monotonic() + 3
            while True:
                try:
                    lock.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise HTTPException(409, "配置正在被另一个进程保存，请稍后重试")
                    time.sleep(0.05)
            try:
                yield
            finally:
                if os.name == "nt":
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _revision(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _env_value(value: object) -> str:
    if value is None:
        return "null"
    text = str(value)
    if re.fullmatch(r"[A-Za-z0-9_./:@+-]+", text):
        return text
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _replace_fields(content: str, updates: dict[str, object]) -> str:
    remaining = dict(updates)
    output = []
    for binding in parse_stream(StringIO(content)):
        if binding.error:
            raise HTTPException(422, "现有 .env 格式无效，请先在本地修正后再保存")
        key = binding.key
        if key in updates:
            if key in remaining:
                output.append(f"{key}={_env_value(remaining.pop(key))}\n")
            continue  # 清理同一被修改字段的重复定义，其他行原样保留。
        output.append(binding.original.string)
    text = "".join(output)
    if text and not text.endswith("\n"):
        text += "\n"
    return text + "".join(f"{key}={_env_value(value)}\n" for key, value in remaining.items())


class ModelSettingsStore:
    """读取脱敏状态；只把白名单字段和显式输入的新密钥写到 .env。"""

    def __init__(self, env_file: Path) -> None:
        self.path = env_file_path(env_file)

    def read(self) -> dict:
        with _locked_env(self.path):
            return self._view()

    def _view(self) -> dict:
        settings = load_settings(self.path)
        llm = settings.llm
        content = self.path.read_text(encoding="utf-8") if self.path.is_file() else ""
        configured = bool(llm.api_key and llm.api_key.get_secret_value().strip())
        overrides = [name for name, env in EDITABLE_FIELDS.items() if env in os.environ]
        source = "environment" if llm.api_key_env in os.environ else "file" if configured else "missing"
        return {
            "values": {
                name: getattr(
                    llm if hasattr(llm, name) else settings.orchestrator, name
                )
                for name in EDITABLE_FIELDS
            },
            "api_key_configured": configured, "api_key_source": source,
            "api_key_env": llm.api_key_env, "overridden_fields": overrides,
            "env_file": str(self.path), "revision": _revision(content),
        }

    def update(self, request: SettingsUpdate) -> dict:
        with _locked_env(self.path):
            current = self._view()
            if request.revision is not None and request.revision != current["revision"]:
                raise HTTPException(409, "配置已被其他页面修改，请重新加载后再保存")
            if set(request.values) - EDITABLE_FIELDS.keys():
                raise HTTPException(422, "包含不允许从网页修改的配置字段")
            if set(request.values) & set(current["overridden_fields"]):
                raise HTTPException(409, "字段受进程环境变量覆盖，请在启动环境中修改")
            new_key = request.api_key.get_secret_value().strip() if request.api_key else ""
            if request.clear_api_key and new_key:
                raise HTTPException(422, "不能同时清除和设置 API Key")
            if len(new_key) > 8192 or any(ord(char) < 32 or ord(char) == 127 for char in new_key):
                raise HTTPException(422, "API Key 过长或包含控制字符")
            if (new_key or request.clear_api_key) and current["api_key_source"] == "environment":
                raise HTTPException(409, "API Key 来自进程环境变量，请在启动环境中修改")
            try:
                proposed = {**current["values"], **request.values}
                validated = LLMSettings.model_validate({
                    name: value for name, value in proposed.items()
                    if name != "max_workers"
                })
                validated_orchestrator = OrchestratorSettings.model_validate({
                    "max_workers": proposed["max_workers"]
                })
            except ValidationError as exc:
                errors = "; ".join(
                    f"{'.'.join(map(str, item['loc'])) or 'token 预算'}: {item['msg']}"
                    for item in exc.errors(include_input=False, include_context=False)
                )
                raise HTTPException(422, errors) from None
            updates = {
                EDITABLE_FIELDS[name]: (
                    validated_orchestrator.max_workers
                    if name == "max_workers" else getattr(validated, name)
                )
                for name in request.values
            }
            if new_key or request.clear_api_key:
                updates[current["api_key_env"]] = new_key
            if updates:
                content = self.path.read_text(encoding="utf-8") if self.path.is_file() else ""
                text = _replace_fields(content, updates)
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(
                        mode="w", encoding="utf-8", dir=self.path.parent,
                        prefix=f".{self.path.name}.", suffix=".tmp", delete=False,
                    ) as stream:
                        temporary = Path(stream.name)
                        stream.write(text)
                        stream.flush()
                        os.fsync(stream.fileno())
                    if self.path.exists():
                        os.chmod(temporary, self.path.stat().st_mode & 0o777)
                    temporary.replace(self.path)
                finally:
                    if temporary is not None and temporary.exists():
                        temporary.unlink()
            return self._view()


class _PrivateSettingsRoute(APIRoute):
    """禁止配置响应缓存，并避免 FastAPI 校验错误回显密钥输入。"""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request: Request) -> Response:
            if request.method == "PUT" and request.headers.get("origin"):
                origin = request.headers["origin"]
                allowed = {str(request.base_url).rstrip("/"),
                           "http://127.0.0.1:5173", "http://localhost:5173",
                           "http://127.0.0.1:5174", "http://localhost:5174"}
                if origin not in allowed:
                    raise HTTPException(403, "不允许跨站修改模型设置")
            try:
                response = await handler(request)
            except RequestValidationError:
                raise HTTPException(422, "模型设置参数无效，请检查字段和类型") from None
            except (OSError, UnicodeError, ConfigurationError):
                raise HTTPException(400, "无法读取或保存配置，请检查 .env 格式和文件权限") from None
            response.headers["Cache-Control"] = "no-store"
            return response
        return handle


def install_model_settings_routes(api: FastAPI, env_file: Path | None = None) -> None:
    store = ModelSettingsStore(env_file_path(env_file))
    router = APIRouter(prefix="/api/settings", route_class=_PrivateSettingsRoute)

    @router.get("/llm")
    def read_settings() -> dict:
        return store.read()

    @router.put("/llm")
    def update_settings(request: SettingsUpdate) -> dict:
        return store.update(request)

    # 兼容旧版只修改模型名称的调用方，统一复用同一个安全存储。
    def legacy_view(view: dict) -> dict:
        return {"model": view["values"]["model"], "env_file": view["env_file"],
                "overridden_by_environment": "model" in view["overridden_fields"]}

    @router.get("/model")
    def read_model() -> dict:
        return legacy_view(store.read())

    @router.put("/model")
    def update_model(request: LegacyModelUpdate) -> dict:
        return legacy_view(store.update(SettingsUpdate(values={"model": request.model})))

    api.include_router(router)
