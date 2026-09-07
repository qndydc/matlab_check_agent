"""
Description: Persist semantic session state and validate complete source snapshots.
References: ArtifactStore, MatlabFileDiscovery and Pydantic.
Referenced By: Semantic pipeline, graph and resume entrypoints.
"""

from pathlib import Path
from hashlib import sha256

from pydantic import BaseModel, Field

from matlab_refactor_agent.domain.exceptions import OrchestrationError
from matlab_refactor_agent.workers.scanning import MatlabFileDiscovery


class SemanticCheckpoint(BaseModel):
    preparation_reference: str
    source_fingerprint: str
    exclude_patterns: list[str]
    graph_state: dict = Field(default_factory=dict)


def source_snapshot(root: Path, exclude_patterns: list[str]) -> str:
    """Include additions, deletions and byte changes without decoding source."""
    digest = sha256()
    for relative in MatlabFileDiscovery(exclude_patterns).discover(root).files:
        path = (root / relative).resolve()
        if not path.is_relative_to(root.resolve()):
            raise OrchestrationError(f"源码路径越界: {relative}")
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def validate_source(checkpoint: SemanticCheckpoint, root: Path) -> None:
    if source_snapshot(root, checkpoint.exclude_patterns) != checkpoint.source_fingerprint:
        raise OrchestrationError("源码已变化，不能混用旧断点；请选择完全重跑以重建结构图")
