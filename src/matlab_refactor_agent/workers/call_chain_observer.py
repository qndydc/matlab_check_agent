"""
Description: 以完整 WCC 为边界聚合静态检查、可运行测试和可选差分观察。
References: domain.diagnostics、PythonProjectAssembler。
Referenced By: MatlabToPythonMigrationAgent 与 Observer 测试。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from matlab_refactor_agent.domain.diagnostics import (
    DifferentialObservation,
    ExecutionResult,
    ValidationFact,
)
from matlab_refactor_agent.domain.migration import MigrationWorkUnit

from .python_project_assembler import PythonProjectAssembler

TestRunner = Callable[[MigrationWorkUnit, list[Path]], ExecutionResult]
DifferentialRunner = Callable[
    [MigrationWorkUnit, list[Path]], DifferentialObservation
]


class CallChainObserver:
    """Observer 只报告事实，不决定重试、冻结或人工复核。"""

    def __init__(self, *, test_runner: TestRunner | None = None,
                 differential_runner: DifferentialRunner | None = None) -> None:
        self._test_runner = test_runner
        self._differential_runner = differential_runner

    def observe(
        self, unit: MigrationWorkUnit, files: list[Path]
    ) -> DifferentialObservation:
        facts: list[ValidationFact] = []
        syntax_ok = True
        for path in files:
            try:
                compile(path.read_text(encoding="utf-8"), str(path), "exec")
                facts.append(ValidationFact(kind="syntax", passed=True, actual=str(path)))
            except (OSError, SyntaxError) as exc:
                syntax_ok = False
                facts.append(ValidationFact(
                    kind="syntax", passed=False, actual=str(path), detail=str(exc)
                ))

        unresolved: list[str] = []
        if syntax_ok and files:
            unresolved = PythonProjectAssembler(files[0].parent).check_imports(files)
        facts.append(ValidationFact(
            kind="import", passed=syntax_ok and not unresolved,
            expected=[], actual=unresolved,
            detail="完整 WCC 生成文件的相对 import 检查",
        ))

        if self._test_runner is not None:
            result = self._test_runner(unit, files)
            facts.append(ValidationFact(
                kind="runnable_test", passed=result.succeeded,
                actual={"stdout": result.stdout, "stderr": result.stderr,
                        "exception": result.exception_type},
                detail=result.exception_message or "",
            ))

        if self._differential_runner is not None:
            differential = self._differential_runner(unit, files)
            facts.extend(differential.facts)

        return DifferentialObservation(
            unit_id=unit.unit_id,
            passed=bool(facts) and all(fact.passed for fact in facts),
            facts=facts,
        )


__all__ = ["CallChainObserver", "DifferentialRunner", "TestRunner"]
