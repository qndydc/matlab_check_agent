"""
Description: 比较双端执行结果并输出可持久化的结构化事实。
References: NumPy、ContractValidator、domain.diagnostics。
Referenced By: 迁移验证节点与组件测试。
"""

from __future__ import annotations

import numpy as np

from matlab_refactor_agent.domain.contracts import BehaviorContract
from matlab_refactor_agent.domain.diagnostics import (
    DifferentialObservation, ExecutionResult, ValidationFact,
)

from .contract_validator import ContractValidator


class DifferentialValidator:
    def validate(self, contract: BehaviorContract, matlab: ExecutionResult,
                 python: ExecutionResult) -> DifferentialObservation:
        facts = [
            *[fact.model_copy(update={"kind": f"matlab_{fact.kind}"})
              for fact in ContractValidator().validate(contract, matlab)],
            *[fact.model_copy(update={"kind": f"python_{fact.kind}"})
              for fact in ContractValidator().validate(contract, python)],
        ]
        for index, expected in enumerate(contract.outputs):
            if index >= len(matlab.outputs) or index >= len(python.outputs):
                facts.append(ValidationFact(kind="numeric", passed=False,
                                            detail=f"缺少第 {index + 1} 个输出"))
                continue
            try:
                matched = bool(np.allclose(
                    np.asarray(matlab.outputs[index]), np.asarray(python.outputs[index]),
                    rtol=expected.relative_tolerance,
                    atol=expected.absolute_tolerance,
                    equal_nan=True,
                ))
            except (TypeError, ValueError):
                matched = matlab.outputs[index] == python.outputs[index]
            facts.append(ValidationFact(kind="numeric", passed=matched,
                                        expected=matlab.outputs[index], actual=python.outputs[index]))
        return DifferentialObservation(
            unit_id=contract.unit_id,
            passed=all(fact.passed for fact in facts),
            facts=facts,
        )
