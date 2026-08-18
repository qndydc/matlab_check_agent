"""
Description: 为既有导入路径提供基于 LangGraphWorkflow 的 Orchestrator 兼容名称。
References: orchestration.workflow。
Referenced By: application.services、CLI、集成测试和外部调用方。
"""

from .workflow import LangGraphWorkflow


class Orchestrator(LangGraphWorkflow):
    """作用：保持公共 API 稳定；实际跨阶段编排全部由 LangGraphWorkflow 完成。"""


__all__ = ["Orchestrator"]
