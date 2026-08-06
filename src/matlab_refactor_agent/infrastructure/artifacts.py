"""
Description: 提供 Job 隔离、路径安全且原子写入的 JSON artifact 存储。
References: Pydantic、domain.exceptions.ArtifactError。
Referenced By: Orchestrator、QualityGate 和 WorkerContext。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from matlab_refactor_agent.domain.exceptions import ArtifactError

ModelT = TypeVar("ModelT", bound=BaseModel)


class ArtifactStore:
    """作用：在 Worker 间持久化大对象并只传引用；输入：artifact 根目录；输出：安全读写接口；数据流：Worker 模型 -> JSON 文件 -> 后续 Worker。"""

    def __init__(self, root: Path) -> None:
        """作用：初始化 artifact 根目录；输入：配置路径；输出：ArtifactStore；数据流：配置 -> 绝对根路径/目录创建。"""

        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def write_model(self, job_id: str, name: str, model: BaseModel) -> str:
        """作用：持久化 Pydantic 模型；输入：Job ID、名称和模型；输出：artifact 引用；数据流：模型 -> JSON -> Job 隔离文件。"""

        path = self._job_path(job_id, name)
        payload = json.dumps(model.model_dump(mode="json"), ensure_ascii=False, indent=2)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(payload + "\n", encoding="utf-8")
        temporary.replace(path)
        return str(path)

    def read_model(self, reference: str, model_type: type[ModelT]) -> ModelT:
        """作用：恢复 artifact 模型；输入：引用和模型类型；输出：校验后模型；数据流：路径校验 -> JSON 读取 -> Pydantic。"""

        path = self._resolve_reference(reference)
        try:
            return model_type.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ArtifactError(f"无法读取 artifact {path}: {exc}") from exc

    def _job_path(self, job_id: str, name: str) -> Path:
        """作用：构造 Job 隔离路径；输入：Job ID 和文件名；输出：安全绝对路径；数据流：标识清理 -> 边界检查 -> 目录创建。"""

        if not job_id.isalnum() or Path(name).name != name:
            raise ArtifactError("artifact 的 job_id 或名称不安全")
        job_dir = (self.root / job_id).resolve()
        self._assert_within_root(job_dir)
        job_dir.mkdir(parents=True, exist_ok=True)
        path = (job_dir / name).resolve()
        self._assert_within_root(path)
        return path

    def _resolve_reference(self, reference: str) -> Path:
        """作用：验证 artifact 引用；输入：路径字符串；输出：安全绝对路径；数据流：引用 -> resolve -> 根目录边界检查。"""

        path = Path(reference).expanduser().resolve()
        self._assert_within_root(path)
        if not path.is_file():
            raise ArtifactError(f"artifact 不存在: {path}")
        return path

    def _assert_within_root(self, path: Path) -> None:
        """作用：阻止 artifact 路径越界；输入：待检查路径；输出：无或异常；数据流：绝对路径 -> relative_to -> 安全决策。"""

        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ArtifactError(f"artifact 路径越界: {path}") from exc
