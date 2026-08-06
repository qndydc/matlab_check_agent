"""
Description: 强制检查所有生产与测试 Python 文件的标准模块文档头。
References: Python ast、pathlib、README 开发约定。
Referenced By: pytest 测试发现和 CI。
"""

import ast
from pathlib import Path


def test_all_python_files_have_standard_module_header() -> None:
    """作用：防止新增模块遗漏文件头；输入：src/tests 下 `.py` 文件；输出：断言结果；数据流：源码 -> AST module docstring -> 字段检查。"""

    project_root = Path(__file__).parents[2]
    markers = ("Description:", "References:", "Referenced By:")
    missing: list[str] = []
    for source_root in (project_root / "src", project_root / "tests"):
        for path in sorted(source_root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            docstring = ast.get_docstring(tree, clean=False) or ""
            absent = [marker for marker in markers if marker not in docstring]
            if absent:
                relative = path.relative_to(project_root).as_posix()
                missing.append(f"{relative}: {', '.join(absent)}")
    assert not missing, "缺少标准模块头：\n" + "\n".join(missing)

