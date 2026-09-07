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
from .code_tree import CodeTreeDocument, CodeTreeNode
from .migration import (
    ActContext,
    CallChainContext,
    ConversionStratagem,
    MatlabToPythonOutcome,
    MatlabToPythonPlan,
)
from .contracts import BehaviorContract, ExceptionContract, FileSideEffect, ValueContract
from .diagnostics import DifferentialObservation, ExecutionResult, FailureDiagnostic, ValidationFact
from .orchestration import (
    AnalysisOutcome,
    JobRecord,
    ScanOutcome,
    TaskEnvelope,
    WorkerResult,
)
from .semantics import (
    SemanticAnnotationRequest,
    SemanticPreparationBundle,
    SemanticProgressEvent,
    SemanticProgressLog,
    SemanticQualityReport,
    SemanticUnitQuality,
)

__all__ = [
    "AnalysisResult",
    "CodeTreeDocument",
    "CodeTreeNode",
    "DependencyEdge",
    "FunctionInfo",
    "MatlabFileInfo",
    "MatlabFileManifest",
    "MatlabToPythonOutcome",
    "MatlabToPythonPlan",
    "ActContext",
    "CallChainContext",
    "ConversionStratagem",
    "BehaviorContract",
    "ExceptionContract",
    "FileSideEffect",
    "ValueContract",
    "DifferentialObservation",
    "ExecutionResult",
    "FailureDiagnostic",
    "ValidationFact",
    "ParseChunkResult",
    "ScanResult",
    "AnalysisOutcome",
    "JobRecord",
    "ScanOutcome",
    "TaskEnvelope",
    "WorkerResult",
    "SemanticPreparationBundle",
    "SemanticAnnotationRequest",
    "SemanticProgressEvent",
    "SemanticProgressLog",
    "SemanticQualityReport",
    "SemanticUnitQuality",
]
