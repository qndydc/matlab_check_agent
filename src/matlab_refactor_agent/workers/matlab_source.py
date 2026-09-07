"""
Description: 以统一的编码回退策略读取 MATLAB 文本源码。
References: pathlib.Path、MATLAB 项目常见文本编码。
Referenced By: MaxxMatlabParser、SemanticContextBuilder 和源码读取测试。
"""

from __future__ import annotations

from pathlib import Path


_MATLAB_SOURCE_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "cp1252")


def read_matlab_source(path: Path) -> str:
    """读取 MATLAB 源码，并兼容 UTF-8、中文 Windows 和西文 Windows 编码。"""

    for encoding in _MATLAB_SOURCE_ENCODINGS:
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")
