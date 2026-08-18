"""
Description: 验证 ChangeSetExecutor 只修改隔离副本并可安全重放。
References: ChangeSetExecutor、domain.planning。
Referenced By: pytest 测试发现和 P3 隔离执行验收。
"""

from pathlib import Path

import pytest

from matlab_refactor_agent.domain.exceptions import ChangeSetExecutionError
from matlab_refactor_agent.domain.planning import RefactorOperation, RefactorPlan
from matlab_refactor_agent.workers.dependency_analysis import DependencyAnalyzer
from matlab_refactor_agent.workers.matlab_parser import MaxxMatlabParser
from matlab_refactor_agent.workers.scanning import MatlabProjectScanner
from matlab_refactor_agent.orchestration.changeset_executor import ChangeSetExecutor
from matlab_refactor_agent.orchestration.project_validator import (
    RefactoredProjectValidator,
)


def test_executor_renames_copy_without_touching_source(tmp_path: Path) -> None:
    """作用：验证代码引用改写、注释字符串保护和幂等重放；输入：小型 MATLAB 项目；输出：ChangeSet。"""

    source = tmp_path / "source"
    source.mkdir()
    original = (
        "function y = oldName(x)\n"
        "% oldName remains in comment\n"
        "label = 'oldName';\n"
        "y = x;\n"
        "end\n"
    )
    (source / "oldName.m").write_text(original, encoding="utf-8")
    (source / "caller.m").write_text(
        "value = oldName(1);\n", encoding="utf-8"
    )
    plan = RefactorPlan(
        project_root=str(source),
        symbol_to_module={"oldName": "core"},
        operations=[
            RefactorOperation(
                symbol_id="oldName",
                module_id="core",
                source_path="oldName.m",
                target_path="newName.m",
                proposed_name="newName",
                reason="使用更清晰的名称",
                confidence=0.9,
            )
        ],
        target_tree=["caller.m", "newName.m"],
        minimum_confidence=0.9,
    )
    executor = ChangeSetExecutor(tmp_path / "outputs", [])

    first = executor.execute(job_id="job1", source_root=source, plan=plan)
    second = executor.execute(job_id="job1", source_root=source, plan=plan)

    assert first == second
    assert (source / "oldName.m").read_text(encoding="utf-8") == original
    assert not (source / "newName.m").exists()
    output = Path(first.output_root)
    rewritten = (output / "newName.m").read_text(encoding="utf-8")
    assert "function y = newName(x)" in rewritten
    assert "% oldName remains in comment" in rewritten
    assert "label = 'oldName';" in rewritten
    assert (output / "caller.m").read_text(encoding="utf-8") == (
        "value = newName(1);\n"
    )
    assert first.source_tree_hash_before == first.source_tree_hash_after
    assert first.changed_matlab_files == ["caller.m", "newName.m"]

    baseline = DependencyAnalyzer().analyze(
        MatlabProjectScanner(MaxxMatlabParser(), []).scan(source)
    )
    (output / "caller.m").write_text(
        "value = newName(2);\n", encoding="utf-8"
    )
    validation = RefactoredProjectValidator([], []).validate(
        job_id="job1",
        attempt=0,
        baseline=baseline,
        plan=plan,
        change_set=first,
    ).result
    assert not validation.passed
    assert any(
        item.check_id == "output_integrity" and item.status == "failed"
        for item in validation.checks
    )

    with pytest.raises(ChangeSetExecutionError, match="output_dir"):
        ChangeSetExecutor(source / "generated", []).execute(
            job_id="unsafe", source_root=source, plan=plan
        )
    assert not (source / "generated").exists()
