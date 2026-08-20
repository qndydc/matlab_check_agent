"""
Description: 导出 SemanticAnnotationAgent、上下文构建器和聚合器。
References: semantic_annotation 子模块。
Referenced By: 应用层、agents 默认工厂和测试。
"""

from .agent import SemanticAnnotationAgent
from .aggregator import SemanticAnnotationAggregator
from .context_builder import (
    SemanticContextBuilder,
    SemanticWorkUnitBuilder,
    split_semantic_work_unit,
)

__all__ = [
    "SemanticAnnotationAgent",
    "SemanticAnnotationAggregator",
    "SemanticContextBuilder",
    "SemanticWorkUnitBuilder",
    "split_semantic_work_unit",
]
