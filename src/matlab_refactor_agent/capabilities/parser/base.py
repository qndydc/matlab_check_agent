"""
Description: 定义 MATLAB 单文件解析器协议。
References: pathlib、domain.models.MatlabFileInfo。
Referenced By: matlab_scanner 和解析器实现。
"""

from pathlib import Path
from typing import Protocol

from matlab_refactor_agent.domain.models import MatlabFileInfo


class MatlabParser(Protocol):
    """作用：定义 MATLAB 解析器契约；输入：文件和项目路径；输出：文件模型；数据流：扫描器 -> 解析实现 -> MatlabFileInfo。"""

    def parse_file(self, path: Path, project_root: Path) -> MatlabFileInfo:
        """作用：解析单个源文件；输入：文件及根目录；输出：MatlabFileInfo；数据流：路径 -> 解析器 -> 标准模型。"""
        ...
