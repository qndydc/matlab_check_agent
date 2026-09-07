"""
Description: 在调用 Repair Agent 前以确定性规则分类验证失败。
References: domain.diagnostics。
Referenced By: 迁移路由与组件测试。
"""

from matlab_refactor_agent.domain.diagnostics import (
    DifferentialObservation, FailureDiagnostic,
)


class FailureClassifier:
    _CATEGORY = {
        "shape": "shape", "dtype": "dtype", "numeric": "numeric",
        "complex": "complex", "exception": "exception", "side_effect": "side_effect",
        "import": "import", "assembly": "assembly",
    }

    def classify(self, observation: DifferentialObservation) -> FailureDiagnostic:
        failed = [fact for fact in observation.facts if not fact.passed]
        kind = failed[0].kind.removeprefix("matlab_").removeprefix("python_") if failed else "unknown"
        category = self._CATEGORY.get(kind, "unknown")
        action = "semantic_review" if category in {"shape", "dtype", "exception"} else "repair"
        if category == "unknown":
            action = "manual_review"
        return FailureDiagnostic(
            unit_id=observation.unit_id,
            category=category,
            summary=f"{len(failed)} 个验证事实失败；首个类别为 {kind}",
            evidence=failed,
            repairable=category != "unknown",
            action=action,
        )
