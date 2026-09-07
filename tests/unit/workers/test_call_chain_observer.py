"""
Description: 验证 WCC Observer 将语法和可运行测试结果记录为结构化事实。
References: CallChainObserver、ExecutionResult。
Referenced By: pytest 测试发现。
"""

from pathlib import Path

from matlab_refactor_agent.domain.diagnostics import ExecutionResult
from matlab_refactor_agent.domain.migration import MigrationWorkUnit
from matlab_refactor_agent.workers.call_chain_observer import CallChainObserver


def test_observer_returns_syntax_error_instead_of_raising(tmp_path: Path) -> None:
    source = tmp_path / "broken.py"
    source.write_text("def broken(:\n", encoding="utf-8")

    unit = MigrationWorkUnit(
        unit_id="chain", symbol_ids=["main"], entry_symbols=["main"],
        sccs=[["main"]],
    )
    observation = CallChainObserver().observe(unit, [source])

    assert not observation.passed
    assert any(fact.kind == "syntax" and not fact.passed for fact in observation.facts)


def test_observer_can_include_injected_runnable_tests(tmp_path: Path) -> None:
    source = tmp_path / "valid.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    observer = CallChainObserver(
        test_runner=lambda _unit, _files: ExecutionResult(
            succeeded=True, stdout="1 passed"
        )
    )

    unit = MigrationWorkUnit(
        unit_id="chain", symbol_ids=["main"], entry_symbols=["main"],
        sccs=[["main"]],
    )
    observation = observer.observe(unit, [source])

    assert observation.passed
    assert any(fact.kind == "runnable_test" for fact in observation.facts)
