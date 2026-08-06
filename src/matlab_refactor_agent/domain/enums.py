"""
Description: 定义 MATLAB 对象、解析、Job、Task 和 Worker 状态枚举。
References: Python enum.StrEnum。
Referenced By: 领域模型、Orchestrator、StateManager 和 Workers。
"""

from enum import StrEnum


class MatlabObjectKind(StrEnum):
    """作用：标识 MATLAB 源文件对象类型；输入：解析器分类；输出：稳定字符串枚举；数据流：解析器 -> 领域模型/报告。"""

    FUNCTION = "function"
    SCRIPT = "script"
    CLASS = "class"
    UNKNOWN = "unknown"


class ParseStatus(StrEnum):
    """作用：标识单文件解析完整度；输入：解析执行结果；输出：状态枚举；数据流：解析器 -> 扫描结果/诊断。"""

    PARSED = "parsed"
    PARTIAL = "partial"
    FAILED = "failed"


class WorkerKind(StrEnum):
    """作用：标识调度目标 Worker；输入：任务阶段；输出：稳定 Worker 名称；数据流：Orchestrator -> WorkerPool。"""

    SCANNER = "scanner"
    PARSER = "parser"
    ANALYZER = "analyzer"
    PLANNER = "planner"
    EXECUTOR = "executor"
    VALIDATOR = "validator"
    REPORTER = "reporter"


class JobStatus(StrEnum):
    """作用：标识全局 Job 生命周期；输入：调度事件；输出：持久化状态；数据流：Orchestrator -> StateManager。"""

    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    WAITING_APPROVAL = "waiting_approval"


class TaskStatus(StrEnum):
    """作用：标识单任务生命周期；输入：Worker 执行事件；输出：任务状态；数据流：TaskQueue/WorkerPool -> StateManager。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
