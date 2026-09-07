"""
Description: 为 0.1 接口提供委派到三个独立模块的兼容 Orchestrator 名称。
References: orchestration.workflow。
Referenced By: application.services、CLI、集成测试和外部调用方。
"""

from .workflow import MainWorkflow


class Orchestrator(MainWorkflow):
    """保持公共 API 稳定；新代码优先依赖 analysis/semantics/migration。"""


__all__ = ["Orchestrator"]
