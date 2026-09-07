"""
Description: 提供 WCC 状态存储、断点重置与依赖优先调度。
References: domain.migration、ArtifactStore。
Referenced By: MigrationGraph 与迁移组件测试。
"""

from __future__ import annotations

from threading import RLock

from pydantic import RootModel

from matlab_refactor_agent.domain.migration import MatlabToPythonPlan, MigrationUnitState
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore

MigrationStates = RootModel[list[MigrationUnitState]]
_MIGRATION_STATE_LOCK = RLock()


class MigrationStateStore:
    def __init__(
        self,
        artifacts: ArtifactStore,
        job_id: str,
        reference: str | None = None,
    ) -> None:
        self.artifacts, self.job_id, self.reference = artifacts, job_id, reference

    def initialize(self, plan: MatlabToPythonPlan) -> str:
        return self.save(
            [
                MigrationUnitState(
                    unit_id=unit.unit_id,
                    depends_on_units=unit.depends_on_units,
                )
                for unit in plan.units
            ]
        )

    def load(self) -> list[MigrationUnitState]:
        if not self.reference:
            return []
        return self.artifacts.read_model(self.reference, MigrationStates).root

    def save(self, states: list[MigrationUnitState]) -> str:
        self.reference = self.artifacts.write_model(
            self.job_id, "migration-state.json", MigrationStates(states)
        )
        return self.reference

    def update(self, unit_id: str, **changes: object) -> MigrationUnitState:
        with _MIGRATION_STATE_LOCK:
            states = self.load()
            for index, state in enumerate(states):
                if state.unit_id == unit_id:
                    states[index] = state.model_copy(update=changes)
                    self.save(states)
                    return states[index]
        raise KeyError(unit_id)

    def restart_unfinished(self) -> str:
        """保留已冻结或已有完整复核产物的 WCC，重置真正中断的工作。"""

        states = self.load()
        restarted = [
            state.model_copy(
                update={
                    "status": "pending",
                    "translation_ref": None,
                    "assembly_ref": None,
                    "observation_ref": None,
                    "retry_count": 0,
                }
            )
            if not (
                state.status == "frozen"
                or (
                    state.status == "manual_review"
                    and state.translation_ref
                    and state.assembly_ref
                    and state.observation_ref
                )
            )
            else state
            for state in states
        ]
        return self.save(restarted)


class MigrationScheduler:
    @staticmethod
    def ready(states: list[MigrationUnitState]) -> list[MigrationUnitState]:
        terminal = {
            state.unit_id for state in states
            if state.status in {"frozen", "manual_review"}
        }
        return [
            state for state in states
            if state.status in {"pending", "ready"}
            and set(state.depends_on_units) <= terminal
        ]

    @staticmethod
    def affected_upstream(
        states: list[MigrationUnitState], repaired_unit_id: str
    ) -> set[str]:
        affected: set[str] = set()
        frontier = {repaired_unit_id}
        while frontier:
            current = frontier.pop()
            parents = {
                state.unit_id
                for state in states
                if current in state.depends_on_units
            }
            unseen = parents - affected
            affected.update(unseen)
            frontier.update(unseen)
        return affected
