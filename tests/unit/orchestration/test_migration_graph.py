"""
Description: 验证 WCC 调用链的 Reason、Act、Observation 与冻结顺序。
References: MigrationGraph、MigrationStateStore。
Referenced By: pytest 测试发现。
"""

from pathlib import Path

import pytest

from matlab_refactor_agent.domain.migration import (
    ConversionStratagem,
    MatlabToPythonPlan,
    MigrationWorkUnit,
    PythonArchitecture,
)
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore
from matlab_refactor_agent.orchestration.migration_graph import MigrationGraph
from matlab_refactor_agent.orchestration.migration_state import MigrationStateStore


def test_wcc_graph_runs_reason_act_observe_then_freezes(tmp_path: Path) -> None:
    artifacts = ArtifactStore(tmp_path / "artifacts")
    job_id = "job1"
    unit = MigrationWorkUnit(
        unit_id="chain", symbol_ids=["main", "a"], entry_symbols=["main"],
        sccs=[["a"], ["main"]],
    )
    plan_ref = artifacts.write_model(job_id, "plan.json", MatlabToPythonPlan(
        project_root="p", architecture=PythonArchitecture(
            package_name="p", source_directory="src/p"), units=[unit]))
    order: list[str] = []

    def build_context(chain_id: str, observation_ref: str | None,
                      stratagem_ref: str | None) -> str:
        order.append("build_context")
        return artifacts.write_text(job_id, "context.txt", chain_id)

    def reason(chain_id: str, context_ref: str,
               observation_ref: str | None) -> str:
        order.append("reason")
        action = "finish" if observation_ref else "convert"
        return artifacts.write_model(job_id, f"strategy-{action}.json",
                                     ConversionStratagem(unit_id=chain_id, action=action))

    def act(chain_id: str, context_ref: str, stratagem_ref: str) -> str:
        order.append("act")
        return artifacts.write_text(job_id, "translation.txt", chain_id)

    def assemble(chain_id: str, translation_ref: str) -> str:
        order.append("assemble")
        return artifacts.write_text(job_id, "assembly.txt", chain_id)

    def observe(chain_id: str, assembly_ref: str) -> str:
        from matlab_refactor_agent.domain.diagnostics import DifferentialObservation
        order.append("observe")
        return artifacts.write_model(job_id, "observation.json",
                                     DifferentialObservation(unit_id=chain_id, passed=True))

    result = MigrationGraph(
        artifacts, build_context=build_context, reason=reason, act=act,
        assemble=assemble, observe=observe,
    ).graph.invoke({"job_id": job_id, "plan_ref": plan_ref,
                    "migration_state_ref": "", "retry_count": 0})
    states = MigrationStateStore(
        artifacts, job_id, result["migration_state_ref"]
    ).load()
    assert states[0].status == "frozen"
    assert order == ["build_context", "reason", "act", "assemble", "observe", "reason"]


def test_resume_skips_frozen_wcc_and_starts_from_unfinished_wcc(
    tmp_path: Path,
) -> None:
    artifacts = ArtifactStore(tmp_path / "artifacts")
    job_id = "resumejob"
    units = [
        MigrationWorkUnit(unit_id="first", symbol_ids=["first"]),
        MigrationWorkUnit(
            unit_id="second",
            symbol_ids=["second"],
            depends_on_units=["first"],
        ),
    ]
    plan_ref = artifacts.write_model(
        job_id,
        "plan.json",
        MatlabToPythonPlan(
            project_root="p",
            architecture=PythonArchitecture(
                package_name="p", source_directory="src/p"
            ),
            units=units,
        ),
    )
    visited: list[str] = []
    interrupt_second = True

    def build_context(
        chain_id: str,
        observation_ref: str | None,
        stratagem_ref: str | None,
    ) -> str:
        del observation_ref, stratagem_ref
        visited.append(chain_id)
        if chain_id == "second" and interrupt_second:
            raise RuntimeError("simulated interruption")
        return artifacts.write_text(job_id, f"context-{chain_id}.txt", chain_id)

    def reason(
        chain_id: str, context_ref: str, observation_ref: str | None
    ) -> str:
        del context_ref
        action = "finish" if observation_ref else "convert"
        return artifacts.write_model(
            job_id,
            f"strategy-{chain_id}-{action}.json",
            ConversionStratagem(unit_id=chain_id, action=action),
        )

    def act(chain_id: str, context_ref: str, stratagem_ref: str) -> str:
        del context_ref, stratagem_ref
        return artifacts.write_text(
            job_id, f"translation-{chain_id}.txt", chain_id
        )

    def assemble(chain_id: str, translation_ref: str) -> str:
        del translation_ref
        return artifacts.write_text(
            job_id, f"assembly-{chain_id}.txt", chain_id
        )

    def observe(chain_id: str, assembly_ref: str) -> str:
        from matlab_refactor_agent.domain.diagnostics import DifferentialObservation

        del assembly_ref
        return artifacts.write_model(
            job_id,
            f"observation-{chain_id}.json",
            DifferentialObservation(unit_id=chain_id, passed=True),
        )

    graph = MigrationGraph(
        artifacts,
        build_context=build_context,
        reason=reason,
        act=act,
        assemble=assemble,
        observe=observe,
    ).graph
    with pytest.raises(RuntimeError, match="simulated interruption"):
        graph.invoke({
            "job_id": job_id,
            "plan_ref": plan_ref,
            "migration_state_ref": "",
            "retry_count": 0,
        })

    state_ref = artifacts.reference(job_id, "migration-state.json")
    store = MigrationStateStore(artifacts, job_id, state_ref)
    assert [item.status for item in store.load()] == ["frozen", "pending"]

    visited.clear()
    interrupt_second = False
    resumed = graph.invoke({
        "job_id": job_id,
        "plan_ref": plan_ref,
        "migration_state_ref": store.restart_unfinished(),
        "retry_count": 0,
    })

    assert visited == ["second"]
    assert [
        item.status
        for item in MigrationStateStore(
            artifacts, job_id, resumed["migration_state_ref"]
        ).load()
    ] == ["frozen", "frozen"]


def test_manual_review_still_runs_project_validation_and_blocks_publish(
    tmp_path: Path,
) -> None:
    from matlab_refactor_agent.domain.diagnostics import DifferentialObservation

    artifacts = ArtifactStore(tmp_path / "artifacts")
    job_id = "reviewjob"
    plan_ref = artifacts.write_model(job_id, "plan.json", MatlabToPythonPlan(
        project_root="p", architecture=PythonArchitecture(
            package_name="p", source_directory="src/p"),
        units=[MigrationWorkUnit(unit_id="chain", symbol_ids=["main"])],
    ))
    visited: list[str] = []

    def reason(chain_id: str, context_ref: str,
               observation_ref: str | None) -> str:
        del context_ref
        return artifacts.write_model(
            job_id, "strategy.json",
            ConversionStratagem(
                unit_id=chain_id,
                action="manual_review" if observation_ref else "convert",
            ),
        )

    def project_validate(state_ref: str) -> str:
        del state_ref
        visited.append("project_validate")
        return artifacts.write_model(
            job_id, "project-observation.json",
            DifferentialObservation(unit_id="project", passed=False),
        )

    result = MigrationGraph(
        artifacts,
        build_context=lambda chain_id, *_: artifacts.write_text(
            job_id, "context.txt", chain_id
        ),
        reason=reason,
        act=lambda chain_id, *_: artifacts.write_text(
            job_id, "translation.txt", chain_id
        ),
        assemble=lambda chain_id, *_: artifacts.write_text(
            job_id, "assembly.txt", chain_id
        ),
        observe=lambda chain_id, *_: artifacts.write_model(
            job_id, "observation.json",
            DifferentialObservation(unit_id=chain_id, passed=False),
        ),
        project_validate=project_validate,
        on_step=lambda name, _: visited.append(name),
    ).graph.invoke({"job_id": job_id, "plan_ref": plan_ref,
                    "migration_state_ref": "", "retry_count": 0})

    states = MigrationStateStore(
        artifacts, job_id, result["migration_state_ref"]
    ).load()
    assert states[0].status == "manual_review"
    assert visited.count("project_validate") == 2  # 节点记录一次，校验函数一次
    assert "publish" not in visited

    visited.clear()
    resumed = MigrationGraph(
        artifacts,
        build_context=lambda *_: (_ for _ in ()).throw(
            AssertionError("完整人工复核断点不应重建上下文")
        ),
        reason=lambda *_: (_ for _ in ()).throw(
            AssertionError("完整人工复核断点不应重新规划")
        ),
        act=lambda *_: (_ for _ in ()).throw(
            AssertionError("完整人工复核断点不应重新转换")
        ),
        assemble=lambda *_: (_ for _ in ()).throw(
            AssertionError("完整人工复核断点不应重新组装")
        ),
        observe=lambda *_: (_ for _ in ()).throw(
            AssertionError("完整人工复核断点不应重新检查 WCC")
        ),
        project_validate=project_validate,
        on_step=lambda name, _: visited.append(name),
    ).graph.invoke({
        "job_id": job_id,
        "plan_ref": plan_ref,
        "migration_state_ref": MigrationStateStore(
            artifacts, job_id, result["migration_state_ref"]
        ).restart_unfinished(),
        "retry_count": 0,
    })
    assert resumed["migration_state_ref"] == result["migration_state_ref"]
    assert visited == ["initialize", "select_call_chain", "project_validate",
                       "project_validate"]
