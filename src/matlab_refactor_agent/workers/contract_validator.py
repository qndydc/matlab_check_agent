"""
Description: 对单端执行结果执行确定性的行为契约检查。
References: domain.contracts、domain.diagnostics。
Referenced By: DifferentialValidator。
"""

from __future__ import annotations

from matlab_refactor_agent.domain.contracts import BehaviorContract
from matlab_refactor_agent.domain.diagnostics import ExecutionResult, ValidationFact


class ContractValidator:
    def validate(self, contract: BehaviorContract, result: ExecutionResult) -> list[ValidationFact]:
        facts: list[ValidationFact] = []
        expected_exception = contract.expected_exception
        facts.append(ValidationFact(
            kind="exception",
            passed=(result.exception_type == expected_exception.type_name) if expected_exception else result.succeeded,
            expected=expected_exception.type_name if expected_exception else None,
            actual=result.exception_type,
        ))
        if expected_exception:
            return facts
        for index, expected in enumerate(contract.outputs):
            actual_shape = result.output_shapes[index] if index < len(result.output_shapes) else None
            actual_dtype = result.output_dtypes[index] if index < len(result.output_dtypes) else None
            shape_ok = actual_shape is not None and len(actual_shape) == len(expected.shape) and all(
                wanted is None or wanted == actual for wanted, actual in zip(expected.shape, actual_shape)
            )
            facts.extend([
                ValidationFact(kind="shape", passed=shape_ok, expected=expected.shape, actual=actual_shape),
                ValidationFact(kind="dtype", passed=expected.dtype is None or expected.dtype == actual_dtype,
                               expected=expected.dtype, actual=actual_dtype),
            ])
        actual_effects = {(item["path"], item["operation"]) for item in result.file_side_effects}
        expected_effects = {(item.path, item.operation) for item in contract.file_side_effects}
        facts.append(ValidationFact(kind="side_effect", passed=actual_effects == expected_effects,
                                    expected=sorted(expected_effects), actual=sorted(actual_effects)))
        return facts
