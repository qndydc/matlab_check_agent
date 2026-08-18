"""
Description: 为确定性 Worker 发现 MATLAB 文件，并提供同步扫描解析流程。
References: parser.base、domain.models、fnmatch。
Referenced By: ScannerAgent 和 scanner 单元测试。
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

from matlab_refactor_agent.domain.exceptions import ProjectPathError
from matlab_refactor_agent.domain.models import MatlabFileManifest, ScanResult

from .parser_protocol import MatlabParser


class MatlabFileDiscovery:
    """作用：仅发现和过滤项目内 `.m` 文件；输入：排除规则；输出：MatlabFileManifest；数据流：目录 -> rglob/过滤 -> 轻量清单。"""

    def __init__(self, exclude_patterns: list[str] | None = None) -> None:
        """作用：配置文件发现规则；输入：可选排除模式；输出：Discovery 实例；数据流：项目配置 -> 路径过滤器。"""

        self._exclude_patterns = exclude_patterns or [".git", "slprj", "build"]

    def discover(self, project_root: Path) -> MatlabFileManifest:
        """作用：生成 MATLAB 文件清单；输入：项目根目录；输出：MatlabFileManifest；数据流：路径校验 -> 文件发现/过滤 -> 相对路径列表。"""

        root = _validate_project_root(project_root)
        files: list[str] = []
        excluded_count = 0
        for path in sorted(root.rglob("*.m"), key=lambda item:item.as_posix()):
            relative = path.relative_to(root)
            if self._is_excluded(relative):
                excluded_count += 1
                continue
            files.append(relative.as_posix())
        return MatlabFileManifest(
            project_root=str(root),
            files=files,
            excluded_count=excluded_count,
        )

    def _is_excluded(self, relative: Path) -> bool:
        """作用：判断文件是否排除；输入：相对路径；输出：布尔值；数据流：路径与规则 -> 精确/通配匹配 -> 发现决策。"""

        posix = relative.as_posix()
        for pattern in self._exclude_patterns:
            normalized = pattern.replace("\\", "/").strip("/")
            if any(part == normalized for part in relative.parts):
                return True
            if fnmatch.fnmatch(posix, normalized) or fnmatch.fnmatch(
                posix, f"*/{normalized}/*"
            ):
                return True
        return False


class MatlabProjectScanner:
    """作用：兼容性地发现并同步解析 `.m` 文件；输入：解析器和排除规则；输出：ScanResult；数据流：Discovery -> 逐文件解析 -> 聚合。"""

    def __init__(self, parser: MatlabParser, exclude_patterns: list[str] | None = None) -> None:
        """作用：配置扫描器；输入：解析器与可选排除规则；输出：扫描器实例；数据流：应用配置 -> 扫描策略。"""

        self._parser = parser
        self._discovery = MatlabFileDiscovery(exclude_patterns)

    def scan(self, project_root: Path) -> ScanResult:
        """作用：递归扫描项目；输入：项目根目录；输出：ScanResult；数据流：路径校验 -> `.m` 发现/过滤 -> 解析汇总。"""

        manifest = self._discovery.discover(project_root)
        root = Path(manifest.project_root)
        files = [self._parser.parse_file(root / item, root) for item in manifest.files]
        return ScanResult(
            project_root=manifest.project_root,
            files=files,
            excluded_count=manifest.excluded_count,
        )


def _validate_project_root(project_root: Path) -> Path:
    """作用：验证项目根目录；输入：用户路径；输出：绝对目录；数据流：expand/resolve -> exists/is_dir -> Discovery。"""

    root = project_root.expanduser().resolve()
    if not root.exists():
        raise ProjectPathError(f"项目路径不存在: {root}")
    if not root.is_dir():
        raise ProjectPathError(f"项目路径不是目录: {root}")
    return root
