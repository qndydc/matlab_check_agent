"""
Description: 隔离 maxx，并提取 MATLAB 文件类型、签名、作用域和调用名称。
References: maxx、domain.models、Tree-sitter 解析结果。
Referenced By: MatlabProjectScanner、ScannerAgent 和解析器测试。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from matlab_refactor_agent.domain.enums import MatlabObjectKind, ParseStatus
from matlab_refactor_agent.domain.exceptions import ParserUnavailableError
from matlab_refactor_agent.domain.models import FunctionInfo, MatlabFileInfo, relative_posix

_FUNCTION_RE = re.compile(
    r"^\s*function(?:\s+(?:(?:\[(?P<outputs_list>[^\]]*)\]|(?P<output>\w+))\s*=\s*)?"
    r"(?P<name>[A-Za-z]\w*(?:\.[A-Za-z]\w*)*)"
    r"(?:\s*\((?P<inputs>[^)]*)\))?)?",
    re.MULTILINE,
)
_CLASS_RE = re.compile(r"^\s*classdef(?:\s*\([^)]*\))?\s+(?P<name>[A-Za-z]\w*)", re.MULTILINE)
_CALL_RE = re.compile(r"(?<![.@])\b([A-Za-z]\w*(?:\.[A-Za-z]\w*)*)\s*\(")
_KEYWORDS = {
    "arguments", "break", "case", "catch", "classdef", "continue", "else",
    "elseif", "end", "enumeration", "events", "for", "function", "global",
    "if", "methods", "otherwise", "parfor", "persistent", "properties",
    "return", "spmd", "switch", "try", "while",
}


class MaxxMatlabParser:
    """作用：适配 maxx 并标准化 MATLAB 元数据；输入：源文件；输出：MatlabFileInfo；数据流：源码 -> maxx/保守提取 -> 领域模型。"""

    def __init__(self) -> None:
        """作用：加载并隔离 maxx；输入：已安装依赖；输出：解析器实例；数据流：Python 导入 -> FileParser 适配器。"""

        try:
            from maxx.treesitter import FileParser
            from loguru import logger as loguru_logger
        except ImportError as exc:
            raise ParserUnavailableError(
                "未安装 maxx；请执行 `python -m pip install -e .`。"
            ) from exc
        # maxx emits DEBUG/INFO records by default through Loguru. The CLI owns
        # user-facing logging, so keep dependency internals quiet.
        loguru_logger.disable("maxx")
        self._file_parser: Any = FileParser

    def parse_file(self, path: Path, project_root: Path) -> MatlabFileInfo:
        """作用：解析单个 MATLAB 文件；输入：文件与项目根目录；输出：MatlabFileInfo；数据流：读取源码 -> maxx 分类 -> 签名/调用提取。"""

        source = _read_matlab_source(path)
        diagnostics: list[str] = []
        status = ParseStatus.PARSED
        maxx_object: Any | None = None
        try:
            maxx_object = self._file_parser(path).parse()
        except Exception as exc:  # maxx has no stable public exception hierarchy
            status = ParseStatus.PARTIAL
            diagnostics.append(f"maxx 解析失败，已使用保守提取: {exc}")

        kind = _detect_kind(source, maxx_object)
        package = _package_name(path, project_root)
        class_match = _CLASS_RE.search(source)
        class_name = class_match.group("name") if class_match else None
        functions = _extract_functions(
            source, path, project_root, package, kind, class_name
        )
        if not functions and kind == MatlabObjectKind.SCRIPT:
            name = path.stem
            functions = [
                FunctionInfo(
                    name=name,
                    qualified_name=_qualify(package, name),
                    file_path=relative_posix(path, project_root),
                    kind=MatlabObjectKind.SCRIPT,
                    calls=_extract_calls(source),
                    line_count=len(source.splitlines()),
                    start_line=1,
                    end_line=max(1, len(source.splitlines())),
                )
            ]

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
    """作用：兼容读取 MATLAB 源码；输入：文件路径；输出：文本；数据流：字节 -> 编码探测 -> Unicode。"""

    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def _detect_kind(source: str, obj: Any | None) -> MatlabObjectKind:
    """作用：确定文件类型；输入：源码与 maxx 对象；输出：类型枚举；数据流：maxx 类型/语法特征 -> 分类。"""

    raw_kind = str(getattr(obj, "kind", "")).lower()
    if "class" in raw_kind or _CLASS_RE.search(source):
        return MatlabObjectKind.CLASS
    if "function" in raw_kind or _FUNCTION_RE.search(source):
        return MatlabObjectKind.FUNCTION
    if "script" in raw_kind or source.strip():
        return MatlabObjectKind.SCRIPT
    return MatlabObjectKind.UNKNOWN


def _package_name(path: Path, root: Path) -> str | None:
    """作用：提取 MATLAB 包名；输入：文件与根目录；输出：点分包名或空；数据流：相对路径 `+` 目录 -> 包名。"""

    parts = path.resolve().relative_to(root.resolve()).parts[:-1]
    packages = [part[1:] for part in parts if part.startswith("+") and len(part) > 1]
    return ".".join(packages) or None


def _qualify(package: str | None, name: str) -> str:
    """作用：生成包限定名；输入：可选包名和对象名；输出：稳定标识；数据流：名称片段 -> 调用图节点名。"""

    return f"{package}.{name}" if package else name


def _split_names(value: str | None) -> list[str]:
    """作用：拆分 MATLAB 参数列表；输入：逗号分隔文本；输出：参数名列表；数据流：签名文本 -> 清理/过滤 -> 模型字段。"""

    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip() and item.strip() != "~"]


def _extract_functions(
    source: str,
    path: Path,
    root: Path,
    package: str | None,
    file_kind: MatlabObjectKind,
    class_name: str | None,
) -> list[FunctionInfo]:
    """作用：提取函数及局部/类作用域；输入：源码、路径和类型；输出：FunctionInfo 列表；数据流：声明匹配 -> 作用域/签名/调用 -> 模型。"""

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
            class_scope = _qualify(package, class_name)
            qualified_name = f"{class_scope}.{name}"
        elif index > 0 and functions:
            qualified_name = f"{functions[0].qualified_name}>{name}"
        else:
            qualified_name = _qualify(package, name)
        functions.append(
            FunctionInfo(
                name=name,
                qualified_name=qualified_name,
                file_path=relative_posix(path, root),
                kind=(MatlabObjectKind.CLASS if file_kind == MatlabObjectKind.CLASS else MatlabObjectKind.FUNCTION),
                inputs=_split_names(match.group("inputs")),
                outputs=outputs,
                calls=[call for call in _extract_calls(body) if call != name],
                line_count=end_line - start_line + 1,
                start_line=start_line,
                end_line=end_line,
            )
        )
    return functions


def _extract_calls(source: str) -> list[str]:
    """作用：提取显式函数调用；输入：MATLAB 源码片段；输出：去重调用名；数据流：源码 -> 注释字符串清理 -> 调用匹配。"""

    sanitized = _strip_comments_and_strings(source)
    calls = {
        match.group(1)
        for match in _CALL_RE.finditer(sanitized)
        if match.group(1).lower() not in _KEYWORDS
    }
    return sorted(calls)


def _strip_comments_and_strings(source: str) -> str:
    """作用：屏蔽注释和字符串；输入：MATLAB 源码；输出：保留行结构的清理文本；数据流：逐字符状态机 -> 调用提取输入。"""

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
