"""
Description: 将结构化生成结果安全组装到隔离 Python 工程。
References: AST、domain.migration、domain.exceptions。
Referenced By: MainWorkflow 与组件测试。
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from matlab_refactor_agent.domain.exceptions import OrchestrationError
from matlab_refactor_agent.domain.migration import TranslationResponse


class PythonProjectAssembler:
    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root.resolve()

    def assemble(self, translation: TranslationResponse) -> list[Path]:
        written: list[Path] = []
        for generated in translation.files:
            relative = PurePosixPath(generated.path.replace("\\", "/"))
            if relative.is_absolute() or ".." in relative.parts or relative.suffix != ".py":
                raise OrchestrationError(f"不安全的生成路径: {generated.path}")
            target = (self.output_root / Path(*relative.parts)).resolve()
            try:
                target.relative_to(self.output_root)
            except ValueError as exc:
                raise OrchestrationError(f"生成路径越界: {generated.path}") from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and target.read_text(encoding="utf-8") != generated.content:
                raise OrchestrationError(f"目标模块存在符号覆盖冲突: {generated.path}")
            temporary = target.with_suffix(target.suffix + ".tmp")
            temporary.write_text(generated.content, encoding="utf-8")
            temporary.replace(target)
            written.append(target)
        return written

    def check_imports(self, files: list[Path]) -> list[str]:
        import ast

        available = {path.stem for path in files}
        unresolved: set[str] = set()
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level and node.module:
                    root = node.module.split(".")[0]
                    if root not in available:
                        unresolved.add(node.module)
        return sorted(unresolved)
