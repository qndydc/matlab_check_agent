"""
Description: 定义隔离代码库验证证据和受限修复提议契约。
References: Pydantic、domain.models、domain.planning。
Referenced By: RefactoredProjectValidator、RepairAgent 和 LangGraphWorkflow。
"""

from typing import Literal

from pydantic import Field

from .models import DomainModel
from .planning import RefactorOperation


class ValidationCheck(DomainModel):
    """作用：记录单项工具验证；输入：工具事实；输出：明确的四态证据。"""

    check_id: str
    status: Literal["passed", "failed", "skipped", "unavailable"]
    summary: str
    details: list[str] = Field(default_factory=list)
    blocking: bool = True


class ValidationResult(DomainModel):
    """作用：汇总一次隔离输出验证；输入：全部检查；输出：闭环路由依据。"""

    schema_version: str = "1.0"
    job_id: str
    attempt: int = Field(ge=0)
    output_root: str
    passed: bool
    checks: list[ValidationCheck]
    diagnostics: list[str] = Field(default_factory=list)


class RepairProposal(DomainModel):
    """作用：约束 RepairAgent 的完整替换操作建议；输入：失败证据；输出：待人工复审提议。"""

    attempt: int = Field(ge=1)
    summary: str
    addressed_checks: list[str] = Field(min_length=1)
    operations: list[RefactorOperation]
    confidence: float = Field(ge=0.0, le=1.0)
