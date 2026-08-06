"""
Description: 登记 Worker 路径读写声明并检测不兼容并发访问。
References: domain.orchestration.PathClaim、domain.exceptions.ConflictError。
Referenced By: Orchestrator 和组件测试。
"""

from collections import defaultdict

from matlab_refactor_agent.domain.exceptions import ConflictError
from matlab_refactor_agent.domain.orchestration import PathClaim


class ConflictResolver:
    """作用：检测并仲裁 Worker 路径声明；输入：PathClaim；输出：已接受声明或冲突异常；数据流：任务计划 -> 路径索引 -> 调度门禁。"""

    def __init__(self) -> None:
        """作用：初始化声明索引；输入：无；输出：ConflictResolver；数据流：空状态 -> path claims。"""

        self._claims: dict[str, list[PathClaim]] = defaultdict(list)

    def claim(self, claim: PathClaim) -> None:
        """作用：登记资源声明；输入：PathClaim；输出：无或 ConflictError；数据流：同路径历史声明 -> 读写兼容判断 -> 索引。"""

        existing = self._claims[claim.path]
        conflicts = [
            item
            for item in existing
            if item.task_id != claim.task_id
            and (item.operation != "read" or claim.operation != "read")
        ]
        if conflicts:
            owners = ", ".join(item.task_id for item in conflicts)
            raise ConflictError(
                f"路径冲突 {claim.path}: {claim.task_id} 与 {owners}"
            )
        existing.append(claim)

    def release(self, task_id: str) -> None:
        """作用：释放任务全部声明；输入：Task ID；输出：无；数据流：声明索引 -> 过滤任务 -> 可复用资源。"""

        for path in list(self._claims):
            self._claims[path] = [
                item for item in self._claims[path] if item.task_id != task_id
            ]
            if not self._claims[path]:
                del self._claims[path]
