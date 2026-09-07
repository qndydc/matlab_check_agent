"""
Description: 验证 ActChunk 的 SCC 拓扑装箱、预算和失败二分断点。
References: migration.chunking、domain.migration。
Referenced By: pytest 测试发现。
"""

from pathlib import Path

from matlab_refactor_agent.domain.migration import (
    CallChainContext,
    ConversionStratagem,
    MigrationWorkUnit,
    PythonArchitecture,
    TranslationFunctionContext,
)
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore
from matlab_refactor_agent.migration.chunking import (
    ActChunkLimits,
    ActChunkPlanner,
    ActChunkStore,
)


def _context() -> tuple[CallChainContext, ConversionStratagem]:
    symbols = list("abcdefgh")
    unit = MigrationWorkUnit(
        unit_id="chain",
        symbol_ids=symbols,
        entry_symbols=["a"],
        sccs=[[symbol] for symbol in symbols],
    )
    context = CallChainContext(
        project_root="project",
        unit=unit,
        architecture=PythonArchitecture(
            package_name="project", source_directory="src/project"
        ),
        functions=[
            TranslationFunctionContext(
                symbol_id=symbol,
                file_path=f"{symbol}.m",
                source=f"function y = {symbol}(x)\ny = x + 1;\nend\n",
                source_hash=f"hash-{symbol}",
            )
            for symbol in symbols
        ],
        internal_dependencies=[
            f"{source} -> {target}"
            for source, target in zip(symbols, symbols[1:])
        ],
    )
    strategy = ConversionStratagem(
        unit_id="chain",
        action="convert",
        module_plan={
            symbol: f"project.group_{index // 2}"
            for index, symbol in enumerate(symbols)
        },
    )
    return context, strategy


def test_chunk_planner_respects_limits_and_dependency_order() -> None:
    context, strategy = _context()
    limits = ActChunkLimits(functions=2, modules=2)
    plan = ActChunkPlanner(limits).build(context, strategy)

    leaves = ActChunkStore.leaves(plan)
    assert len(leaves) == 4
    assert {symbol for chunk in leaves for symbol in chunk.symbol_ids} == set("abcdefgh")
    assert all(chunk.function_count <= 2 for chunk in leaves)
    assert all(chunk.module_count <= 2 for chunk in leaves)
    assert all(chunk.input_tokens <= limits.input_tokens for chunk in leaves)
    assert all(chunk.output_tokens <= limits.output_tokens for chunk in leaves)
    owner = {
        symbol: chunk.chunk_id for chunk in leaves for symbol in chunk.symbol_ids
    }
    for source, target in zip("abcdefgh", "bcdefgh"):
        if owner[source] != owner[target]:
            source_chunk = next(
                chunk for chunk in leaves if chunk.chunk_id == owner[source]
            )
            assert owner[target] in source_chunk.depends_on_chunks


def test_failed_chunk_is_superseded_without_losing_other_frozen_chunks(
    tmp_path: Path,
) -> None:
    context, strategy = _context()
    planner = ActChunkPlanner(ActChunkLimits(functions=4, modules=4))
    plan = planner.build(context, strategy)
    artifacts = ArtifactStore(tmp_path / "artifacts")
    store = ActChunkStore(artifacts, "chunkjob", "chain")
    store.initialize(plan)
    parent = ActChunkStore.leaves(plan)[0]
    other = ActChunkStore.leaves(plan)[-1]
    store.update(other.chunk_id, status="frozen")
    updated = store.split(planner, parent.chunk_id, context, strategy)

    replaced = next(item for item in updated.chunks if item.chunk_id == parent.chunk_id)
    children = [
        item for item in updated.chunks if item.parent_chunk_id == parent.chunk_id
    ]
    assert replaced.status == "superseded"
    assert len(children) == 2
    assert {symbol for item in children for symbol in item.symbol_ids} == set(
        parent.symbol_ids
    )
    assert next(
        item for item in updated.chunks if item.chunk_id == other.chunk_id
    ).status == "frozen"
