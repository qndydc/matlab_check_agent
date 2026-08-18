"""
Description: 验证 MATLAB 签名、调用、package、局部函数和类方法解析。
References: MaxxMatlabParser、临时 MATLAB 源码。
Referenced By: pytest 测试发现。
"""

from pathlib import Path

from matlab_refactor_agent.workers.matlab_parser import MaxxMatlabParser


def _parser_without_dependency() -> MaxxMatlabParser:
    """构造真实 Tree-sitter 解析器，测试 AST 提取而非正则路径。"""

    return MaxxMatlabParser()


class _FailingTreeParser:
    """强制 AST 失败，用于验证正则仅作为降级方案。"""

    def parse(self, source: bytes):
        raise SyntaxError("forced failure")


def test_extracts_signature_local_function_and_calls(tmp_path: Path) -> None:
    """作用：验证签名和调用提取；输入：临时 MATLAB 文件；输出：断言结果；数据流：源码 -> 解析 -> 元数据。"""

    source = """function [total, count] = calculate(values, scale)
% ignoredCall(values)
text = 'alsoIgnored(values)';
scaled = helper(values) * scale;
total = sum(scaled);
count = numel(values);
end

function output = helper(values)
output = values;
end
"""
    path = tmp_path / "calculate.m"
    path.write_text(source, encoding="utf-8")

    result = _parser_without_dependency().parse_file(path, tmp_path)

    assert [item.name for item in result.functions] == ["calculate", "helper"]
    assert result.functions[0].inputs == ["values", "scale"]
    assert result.functions[0].outputs == ["total", "count"]
    assert result.functions[0].calls == ["helper", "numel", "sum"]
    assert "ignoredCall" not in result.functions[0].calls


def test_builds_package_qualified_name(tmp_path: Path) -> None:
    """作用：验证包限定名；输入：`+tools` 样例；输出：断言结果；数据流：路径 -> 包提取 -> 节点名。"""

    package = tmp_path / "+tools"
    package.mkdir()
    path = package / "clean.m"
    path.write_text("function y = clean(x)\ny = x;\nend\n", encoding="utf-8")

    result = _parser_without_dependency().parse_file(path, tmp_path)

    assert result.package == "tools"
    assert result.functions[0].qualified_name == "tools.clean"


def test_scopes_local_functions_to_primary_function(tmp_path: Path) -> None:
    """作用：验证局部函数作用域；输入：双函数文件；输出：断言结果；数据流：声明 -> 文件作用域节点名。"""

    path = tmp_path / "primary.m"
    path.write_text(
        "function y = primary(x)\ny = helper(x);\nend\n"
        "function y = helper(x)\ny = x;\nend\n",
        encoding="utf-8",
    )

    result = _parser_without_dependency().parse_file(path, tmp_path)

    assert [item.qualified_name for item in result.functions] == [
        "primary",
        "primary>helper",
    ]


def test_scopes_class_methods_to_class(tmp_path: Path) -> None:
    """作用：验证类方法作用域；输入：classdef 样例；输出：断言结果；数据流：类/方法声明 -> 类限定节点名。"""

    path = tmp_path / "Counter.m"
    path.write_text(
        "classdef Counter\nmethods\n"
        "function obj = Counter()\nend\n"
        "function value = current(obj)\nvalue = 1;\nend\n"
        "end\nend\n",
        encoding="utf-8",
    )

    result = _parser_without_dependency().parse_file(path, tmp_path)

    assert [item.qualified_name for item in result.functions] == [
        "Counter.Counter",
        "Counter.current",
    ]


def test_regex_is_used_only_after_ast_failure(tmp_path: Path) -> None:
    path = tmp_path / "fallback.m"
    path.write_text("function y = fallback(x)\ny = helper(x);\nend\n", encoding="utf-8")
    parser = MaxxMatlabParser.__new__(MaxxMatlabParser)
    parser._tree_parser = _FailingTreeParser()

    result = parser.parse_file(path, tmp_path)

    assert str(result.parse_status) == "partial"
    assert result.functions[0].name == "fallback"
    assert "正则降级" in result.diagnostics[0]
