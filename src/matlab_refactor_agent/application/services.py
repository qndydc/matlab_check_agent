"""
Description: 保留旧 AnalysisService 名称并转发到独立 SemanticService。
References: application.semantic_service。
Referenced By: 旧 CLI、测试和外部调用方。
"""

from .semantic_service import SemanticService


class AnalysisService(SemanticService):
    """兼容旧名称；新代码应直接依赖 SemanticService。"""


__all__ = ["AnalysisService"]
