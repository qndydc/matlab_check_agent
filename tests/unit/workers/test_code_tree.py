"""
Description: 验证项目、目录、文件和符号四级代码树的稳定构建。
References: CodeTreeBuilder、domain.models。
Referenced By: pytest 测试发现。
"""

from matlab_refactor_agent.domain.models import AnalysisResult, FunctionInfo
from matlab_refactor_agent.workers.code_tree import CodeTreeBuilder


def test_code_tree_builds_directory_file_and_symbol_levels() -> None:
    analysis = AnalysisResult(
        project_root="D:/project",
        functions=[
            FunctionInfo(name="main", qualified_name="main", file_path="main.m"),
            FunctionInfo(
                name="normalize",
                qualified_name="utils.normalize",
                file_path="+utils/normalize.m",
            ),
        ],
    )

    document = CodeTreeBuilder().build(
        analysis, summaries={"utils.normalize": "归一化输入矩阵"}
    )

    assert document.file_count == 2
    assert document.symbol_count == 2
    assert [item.level for item in document.root.children] == ["directory", "file"]
    package = document.root.children[0]
    assert package.path == "+utils"
    assert package.children[0].children[0].symbol_id == "utils.normalize"
    assert package.children[0].children[0].summary == "归一化输入矩阵"

