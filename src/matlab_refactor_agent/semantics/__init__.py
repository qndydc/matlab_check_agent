"""
Description: 延迟导出三级语义生成与自检流水线；该模块不是自主 Agent。
References: semantics.annotator、quality、aggregator、pipeline。
Referenced By: MainWorkflow、SemanticService 和 Migration Agent 可选记忆读取。
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORT_MODULES = {
    "ClusterSemanticAnnotator": ".annotator",
    "SemanticAggregator": ".aggregator",
    "SemanticAnnotationPipeline": ".pipeline",
    "SemanticPipelineResult": ".pipeline",
    "SemanticPreparationStage": ".preparation",
    "SemanticQualityDecision": ".quality",
    "SemanticQualityReviewer": ".quality",
}

__all__ = list(_EXPORT_MODULES)


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value
