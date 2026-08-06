"""
Description: 汇总导出分析与调度领域契约。
References: domain.models、domain.orchestration。
Referenced By: Orchestrator、Workers、能力层和外部调用方。
"""

from .models import (
    AnalysisResult,
    DependencyEdge,
    FunctionInfo,
    MatlabFileInfo,
    MatlabFileManifest,
    ParseChunkResult,
    ScanResult,
)
from .orchestration import (
    AnalysisOutcome,
    JobRecord,
    PathClaim,
    ScanOutcome,
    TaskEnvelope,
    WorkerResult,
)

__all__ = [
    "AnalysisResult",
    "DependencyEdge",
    "FunctionInfo",
    "MatlabFileInfo",
    "MatlabFileManifest",
    "ParseChunkResult",
    "ScanResult",
    "AnalysisOutcome",
    "JobRecord",
    "PathClaim",
    "ScanOutcome",
    "TaskEnvelope",
    "WorkerResult",
]
