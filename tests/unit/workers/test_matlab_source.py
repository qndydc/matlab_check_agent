"""
Description: 验证 MATLAB 源码读取在 parser 与三级语义上下文之间保持一致。
References: read_matlab_source、SemanticContextBuilder、FunctionInfo。
Referenced By: pytest 测试发现。
"""

from pathlib import Path

from matlab_refactor_agent.agents.semantic_annotation.context_builder import (
    SemanticContextBuilder,
)
from matlab_refactor_agent.domain.models import FunctionInfo
from matlab_refactor_agent.workers.matlab_source import read_matlab_source


def test_reads_cp1252_degree_symbol(tmp_path: Path) -> None:
    path = tmp_path / "angle.m"
    source = "% include degree symbol: °\nfunction y = angle(x)\ny = x;\nend\n"
    path.write_bytes(source.encode("cp1252"))

    assert read_matlab_source(path) == source


def test_semantic_context_reuses_matlab_source_decoding(tmp_path: Path) -> None:
    path = tmp_path / "angle.m"
    source = "% include degree symbol: °\nfunction y = angle(x)\ny = x;\nend\n"
    path.write_bytes(source.encode("cp1252"))
    function = FunctionInfo(
        name="angle",
        qualified_name="angle",
        file_path="angle.m",
        inputs=["x"],
        outputs=["y"],
        start_line=1,
        end_line=4,
    )

    context = SemanticContextBuilder._source_context(tmp_path, function)

    assert "degree symbol: °" in context.source
