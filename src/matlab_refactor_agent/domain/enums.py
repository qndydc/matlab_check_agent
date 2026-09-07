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


class AgentKind(StrEnum):
    """作用：标识真正执行迁移决策的 Agent；语义值仅保留 0.1 兼容。"""

    # 已弃用：生产语义流水线使用 SemanticAnnotationRequest。
    SEMANTIC_ANNOTATION = "semantic_annotation"
    MATLAB_TO_PYTHON = "matlab_to_python"


class JobStatus(StrEnum):
    """作用：标识全局 Job 生命周期；输入：调度事件；输出：持久化状态；数据流：Orchestrator -> StateManager。"""

    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskStatus(StrEnum):
    """作用：标识主 Workflow 内单任务生命周期。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
