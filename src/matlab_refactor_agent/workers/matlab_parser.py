"""
Description: 以 Tree-sitter AST 为事实源解析 MATLAB，语法树失败时才使用正则降级。
References: maxx.treesitter、domain.models、Tree-sitter Node。
Referenced By: MatlabProjectScanner、ParserAgent 和解析器测试。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from matlab_refactor_agent.domain.enums import MatlabObjectKind, ParseStatus
from matlab_refactor_agent.domain.exceptions import ParserUnavailableError
from matlab_refactor_agent.domain.models import FunctionInfo, MatlabFileInfo, relative_posix

# 以下正则只服务于 AST 解析失败后的保守降级路径。
_FUNCTION_RE = re.compile(
    r"^\s*function(?:\s+(?:(?:\[(?P<outputs_list>[^\]]*)\]|(?P<output>\w+))\s*=\s*)?"
    r"(?P<name>[A-Za-z]\w*(?:\.[A-Za-z]\w*)*)"
    r"(?:\s*\((?P<inputs>[^)]*)\))?)?",
    re.MULTILINE,
)
_CLASS_RE = re.compile(
    r"^\s*classdef(?:\s*\([^)]*\))?\s+(?P<name>[A-Za-z]\w*)",
    re.MULTILINE,
)
_CALL_RE = re.compile(r"(?<![.@])\b([A-Za-z]\w*(?:\.[A-Za-z]\w*)*)\s*\(")
_KEYWORDS = {
    "arguments", "break", "case", "catch", "classdef", "continue", "else",
    "elseif", "end", "enumeration", "events", "for", "function", "global",
    "if", "methods", "otherwise", "parfor", "persistent", "properties",
    "return", "spmd", "switch", "try", "while",
}


class MaxxMatlabParser:
    """作用：通过 maxx 提供的 Tree-sitter Parser 生成标准 MATLAB 元数据。"""

    def __init__(self) -> None:
        try:
            from maxx.treesitter import PARSER
            from loguru import logger as loguru_logger
        except ImportError as exc:
            raise ParserUnavailableError(
                "未安装 maxx；请执行 `python -m pip install -e .`。"
            ) from exc
        loguru_logger.disable("maxx")
        self._tree_parser: Any = PARSER

    def parse_file(self, path: Path, project_root: Path) -> MatlabFileInfo:
        """优先从 AST 提取文件元数据；仅在 AST 失败时调用正则降级器。"""

        source = _read_matlab_source(path)
        diagnostics: list[str] = []
        package = _package_name(path, project_root)
        status = ParseStatus.PARSED
        try:
            tree = self._tree_parser.parse(source.encode("utf-8"))
            root = tree.root_node
            if root is None or root.has_error:
                raise SyntaxError("Tree-sitter AST 包含 ERROR/MISSING 节点")
            kind = _detect_kind_ast(root, source)
            functions = _extract_functions_ast(
                root, source, path, project_root, package, kind
            )
            if not functions and kind == MatlabObjectKind.SCRIPT:
                functions = [_script_from_ast(root, source, path, project_root, package)]
        except Exception as exc:
            status = ParseStatus.PARTIAL
            diagnostics.append(f"Tree-sitter AST 解析失败，已使用正则降级: {exc}")
            kind = _detect_kind_fallback(source)
            class_match = _CLASS_RE.search(source)
            class_name = class_match.group("name") if class_match else None
            functions = _extract_functions_fallback(
                source, path, project_root, package, kind, class_name
            )
            if not functions and kind == MatlabObjectKind.SCRIPT:
                functions = [_script_from_fallback(source, path, project_root, package)]

        return MatlabFileInfo(
            path=relative_posix(path, project_root),
            kind=kind,
            package=package,
            functions=functions,
            line_count=len(source.splitlines()),
            parse_status=status,
            diagnostics=diagnostics,
        )


def _read_matlab_source(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def _walk(node: Any, *, stop_at_functions: bool = False) -> Iterable[Any]:
    """按源码顺序遍历 AST；读取函数体调用时不进入嵌套函数定义。"""

    yield node
    for child in node.named_children:
        if stop_at_functions and child.type == "function_definition":
            continue
        yield from _walk(child, stop_at_functions=stop_at_functions)


def _decode_node(node: Any, source: str) -> str:
    encoded = source.encode("utf-8")
    return encoded[node.start_byte : node.end_byte].decode("utf-8")


def _detect_kind_ast(root: Any, source: str) -> MatlabObjectKind:
    top_types = {child.type for child in root.named_children}
    if "class_definition" in top_types:
        return MatlabObjectKind.CLASS
    if "function_definition" in top_types:
        return MatlabObjectKind.FUNCTION
    if source.strip():
        return MatlabObjectKind.SCRIPT
    return MatlabObjectKind.UNKNOWN


def _ancestor(node: Any, node_type: str) -> Any | None:
    current = node.parent
    while current is not None:
        if current.type == node_type:
            return current
        current = current.parent
    return None


def _first_direct_child(node: Any, node_type: str) -> Any | None:
    return next((child for child in node.named_children if child.type == node_type), None)


def _identifier_names(node: Any | None, source: str) -> list[str]:
    if node is None:
        return []
    return [
        _decode_node(item, source)
        for item in _walk(node)
        if item.type == "identifier"
    ]


def _class_name_ast(class_node: Any, source: str) -> str:
    name_node = class_node.child_by_field_name("name")
    if name_node is None:
        name_node = _first_direct_child(class_node, "identifier")
    if name_node is None:
        raise ValueError("class_definition 缺少名称节点")
    return _decode_node(name_node, source)


def _call_name_ast(call_node: Any, source: str) -> str | None:
    expression = call_node
    while expression.parent is not None and expression.parent.type == "field_expression":
        expression = expression.parent
    name = _decode_node(expression, source).strip().split("(", 1)[0].strip()
    if not name or name.lower() in _KEYWORDS:
        return None
    return name


def _extract_calls_ast(node: Any, source: str) -> list[str]:
    calls = {
        name
        for item in _walk(node, stop_at_functions=True)
        if item.type == "function_call"
        if (name := _call_name_ast(item, source)) is not None
    }
    return sorted(calls)


def _extract_functions_ast(
    root: Any,
    source: str,
    path: Path,
    project_root: Path,
    package: str | None,
    file_kind: MatlabObjectKind,
) -> list[FunctionInfo]:
    function_nodes = [item for item in _walk(root) if item.type == "function_definition"]
    top_level_nodes = [node for node in function_nodes if _ancestor(node, "class_definition") is None]
    primary_name: str | None = None
    functions: list[FunctionInfo] = []
    for node in function_nodes:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            name_node = _first_direct_child(node, "identifier")
        if name_node is None:
            raise ValueError("function_definition 缺少名称节点")
        name = _decode_node(name_node, source)
        class_node = _ancestor(node, "class_definition")
        if class_node is not None:
            qualified_name = f"{_qualify(package, _class_name_ast(class_node, source))}.{name}"
        else:
            if node is top_level_nodes[0]:
                primary_name = _qualify(package, name)
                qualified_name = primary_name
            else:
                qualified_name = f"{primary_name}>{name}"
        arguments_node = _first_direct_child(node, "function_arguments")
        output_node = _first_direct_child(node, "function_output")
        start_line = node.start_point.row + 1
        end_line = max(start_line, node.end_point.row + 1)
        functions.append(
            FunctionInfo(
                name=name,
                qualified_name=qualified_name,
                file_path=relative_posix(path, project_root),
                kind=(
                    MatlabObjectKind.CLASS
                    if file_kind == MatlabObjectKind.CLASS
                    else MatlabObjectKind.FUNCTION
                ),
                inputs=_identifier_names(arguments_node, source),
                outputs=_identifier_names(output_node, source),
                calls=[call for call in _extract_calls_ast(node, source) if call != name],
                line_count=end_line - start_line + 1,
                start_line=start_line,
                end_line=end_line,
            )
        )
    return functions


def _script_from_ast(
    root: Any, source: str, path: Path, project_root: Path, package: str | None
) -> FunctionInfo:
    lines = source.splitlines()
    return FunctionInfo(
        name=path.stem,
        qualified_name=_qualify(package, path.stem),
        file_path=relative_posix(path, project_root),
        kind=MatlabObjectKind.SCRIPT,
        calls=_extract_calls_ast(root, source),
        line_count=len(lines),
        start_line=1,
        end_line=max(1, len(lines)),
    )


def _package_name(path: Path, root: Path) -> str | None:
    parts = path.resolve().relative_to(root.resolve()).parts[:-1]
    packages = [part[1:] for part in parts if part.startswith("+") and len(part) > 1]
    return ".".join(packages) or None


def _qualify(package: str | None, name: str) -> str:
    return f"{package}.{name}" if package else name


# ----------------------------- 正则降级路径 -----------------------------

def _detect_kind_fallback(source: str) -> MatlabObjectKind:
    if _CLASS_RE.search(source):
        return MatlabObjectKind.CLASS
    if _FUNCTION_RE.search(source):
        return MatlabObjectKind.FUNCTION
    if source.strip():
        return MatlabObjectKind.SCRIPT
    return MatlabObjectKind.UNKNOWN


def _split_names(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip() and item.strip() != "~"]


def _extract_functions_fallback(
    source: str,
    path: Path,
    root: Path,
    package: str | None,
    file_kind: MatlabObjectKind,
    class_name: str | None,
) -> list[FunctionInfo]:
    matches = list(_FUNCTION_RE.finditer(source))
    lines = source.splitlines()
    functions: list[FunctionInfo] = []
    for index, match in enumerate(matches):
        name = match.group("name")
        if not name:
            continue
        start_line = source.count("\n", 0, match.start()) + 1
        next_start = (
            source.count("\n", 0, matches[index + 1].start()) + 1
            if index + 1 < len(matches)
            else len(lines) + 1
        )
        end_line = max(start_line, next_start - 1)
        body = "\n".join(lines[start_line - 1 : end_line])
        outputs = _split_names(match.group("outputs_list") or match.group("output"))
        if class_name:
            qualified_name = f"{_qualify(package, class_name)}.{name}"
        elif index > 0 and functions:
            qualified_name = f"{functions[0].qualified_name}>{name}"
        else:
            qualified_name = _qualify(package, name)
        functions.append(
            FunctionInfo(
                name=name,
                qualified_name=qualified_name,
                file_path=relative_posix(path, root),
                kind=(
                    MatlabObjectKind.CLASS
                    if file_kind == MatlabObjectKind.CLASS
                    else MatlabObjectKind.FUNCTION
                ),
                inputs=_split_names(match.group("inputs")),
                outputs=outputs,
                calls=[call for call in _extract_calls_fallback(body) if call != name],
                line_count=end_line - start_line + 1,
                start_line=start_line,
                end_line=end_line,
            )
        )
    return functions


def _script_from_fallback(
    source: str, path: Path, project_root: Path, package: str | None
) -> FunctionInfo:
    lines = source.splitlines()
    return FunctionInfo(
        name=path.stem,
        qualified_name=_qualify(package, path.stem),
        file_path=relative_posix(path, project_root),
        kind=MatlabObjectKind.SCRIPT,
        calls=_extract_calls_fallback(source),
        line_count=len(lines),
        start_line=1,
        end_line=max(1, len(lines)),
    )


def _extract_calls_fallback(source: str) -> list[str]:
    sanitized = _strip_comments_and_strings(source)
    return sorted(
        {
            match.group(1)
            for match in _CALL_RE.finditer(sanitized)
            if match.group(1).lower() not in _KEYWORDS
        }
    )


def _strip_comments_and_strings(source: str) -> str:
    result: list[str] = []
    for line in source.splitlines():
        cleaned: list[str] = []
        index = 0
        in_string = False
        while index < len(line):
            char = line[index]
            if char == "'":
                if in_string and index + 1 < len(line) and line[index + 1] == "'":
                    cleaned.extend("  ")
                    index += 2
                    continue
                in_string = not in_string
                cleaned.append(" ")
            elif char == "%" and not in_string:
                cleaned.extend(" " * (len(line) - index))
                break
            else:
                cleaned.append(" " if in_string else char)
            index += 1
        result.append("".join(cleaned))
    return "\n".join(result)
