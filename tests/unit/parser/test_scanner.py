"""
Description: 验证 MATLAB 项目文件发现和排除目录规则。
References: MatlabProjectScanner、FakeParser。
Referenced By: pytest 测试发现。
"""

from pathlib import Path

from matlab_refactor_agent.workers.scanning import MatlabProjectScanner
from matlab_refactor_agent.domain.enums import MatlabObjectKind
from matlab_refactor_agent.domain.models import MatlabFileInfo


class FakeParser:
    """作用：替代真实解析器；输入：测试路径；输出：最小文件模型；数据流：扫描器调用 -> 固定模型。"""

    def parse_file(self, path: Path, project_root: Path) -> MatlabFileInfo:
        """作用：生成测试文件记录；输入：文件和根目录；输出：MatlabFileInfo；数据流：路径 -> 相对路径 -> 模型。"""

        return MatlabFileInfo(
            path=path.relative_to(project_root).as_posix(),
            kind=MatlabObjectKind.SCRIPT,
        )


def test_scanner_excludes_generated_directories(tmp_path: Path) -> None:
    """作用：验证排除目录；输入：临时目录；输出：断言结果；数据流：样例文件 -> 扫描 -> 文件列表/计数。"""

    (tmp_path / "keep.m").write_text("x = 1;", encoding="utf-8")
    build = tmp_path / "build"
    build.mkdir()
    (build / "ignore.m").write_text("x = 2;", encoding="utf-8")

    result = MatlabProjectScanner(FakeParser(), ["build"]).scan(tmp_path)

    assert [item.path for item in result.files] == ["keep.m"]
    assert result.excluded_count == 1
