"""
Description: 使用稳定优先级堆管理待执行 TaskEnvelope。
References: heapq、domain.orchestration.TaskEnvelope。
Referenced By: Orchestrator 和组件测试。
"""

from __future__ import annotations

import heapq
from itertools import count

from matlab_refactor_agent.domain.orchestration import TaskEnvelope


class TaskQueue:
    """作用：按优先级管理待执行任务；输入：TaskEnvelope；输出：下一任务；数据流：Orchestrator 生产 -> 优先队列 -> WorkerPool。"""

    def __init__(self) -> None:
        """作用：初始化内存优先队列；输入：无；输出：TaskQueue；数据流：空状态 -> heap 与稳定序号。"""

        self._items: list[tuple[int, int, TaskEnvelope]] = []
        self._sequence = count()

    def put(self, task: TaskEnvelope) -> None:
        """作用：将任务入队；输入：TaskEnvelope；输出：无；数据流：任务优先级/序号 -> heap。"""

        heapq.heappush(self._items, (task.priority, next(self._sequence), task))

    def get(self) -> TaskEnvelope:
        """作用：取出最高优先级任务；输入：队列状态；输出：TaskEnvelope；数据流：heap -> 最小优先级任务。"""

        if not self._items:
            raise IndexError("任务队列为空")
        return heapq.heappop(self._items)[2]

    def empty(self) -> bool:
        """作用：判断队列是否为空；输入：队列状态；输出：布尔值；数据流：heap 长度 -> 调度决策。"""

        return not self._items

    def __len__(self) -> int:
        """作用：返回待执行任务数；输入：队列状态；输出：整数；数据流：heap -> 指标。"""

        return len(self._items)
