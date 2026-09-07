"""
Description: 验证 Act 输入只保留策略与 WCC 源码，不携带项目结构。
References: CallChainContextBuilder、domain.migration。
Referenced By: pytest 测试发现。
"""

from matlab_refactor_agent.domain.migration import (
    CallChainContext,
    ConversionStratagem,
    MigrationWorkUnit,
    PythonArchitecture,
    TranslationFunctionContext,
)
from matlab_refactor_agent.migration.context import CallChainContextBuilder


def test_act_context_does_not_include_project_structure() -> None:
    unit = MigrationWorkUnit(unit_id="chain", symbol_ids=["main"], sccs=[["main"]])
    context = CallChainContext(
        project_root="p", unit=unit,
        architecture=PythonArchitecture(package_name="p", source_directory="src/p"),
        functions=[TranslationFunctionContext(
            symbol_id="main", file_path="main.m", source="function main\nend",
            source_hash="abc")],
        project_structure=["main -> p.main"],
    )
    act_context = CallChainContextBuilder.for_act(
        context, ConversionStratagem(unit_id="chain", action="convert")
    )

    dumped = act_context.model_dump()
    assert set(dumped) == {
        "unit", "stratagem", "functions", "dependency_functions",
        "dependency_interfaces",
    }
    assert "project_structure" not in dumped
