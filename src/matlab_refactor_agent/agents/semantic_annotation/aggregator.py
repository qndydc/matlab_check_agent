"""
Description: 保留 0.1 版语义聚合器导入路径。
References: matlab_refactor_agent.semantics.aggregator。
Referenced By: 旧测试和外部兼容代码。
"""

from matlab_refactor_agent.semantics.aggregator import (
    SemanticAggregator,
    SemanticAnnotationAggregator,
)

__all__ = ["SemanticAggregator", "SemanticAnnotationAggregator"]
