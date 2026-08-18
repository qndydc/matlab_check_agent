"""
Description: 定义隔离重构执行产生的文件变更和可审计 ChangeSet。
References: Pydantic、domain.models。
Referenced By: ChangeSetExecutor、LangGraphWorkflow 和 CLI 审查输出。
"""

from pydantic import Field

from .models import DomainModel


class AppliedFileChange(DomainModel):
    """作用：记录实际文件移动和符号替换；输入：执行事件；输出：ChangeSet 明细。"""

    source_path: str
    target_path: str
    symbol_ids: list[str] = Field(default_factory=list)
    replacements: dict[str, str] = Field(default_factory=dict)


class ChangeSet(DomainModel):
    """作用：证明计划在隔离输出中落地且源树未改变；输入：快照和执行结果；输出：可重放审计记录。"""

    schema_version: str = "1.0"
    job_id: str
    attempt: int = Field(default=0, ge=0)
    source_root: str
    output_root: str
    plan_hash: str
    source_tree_hash_before: str
    source_tree_hash_after: str
    output_tree_hash: str
    copied_file_count: int = Field(ge=0)
    changed_matlab_files: list[str] = Field(default_factory=list)
    file_changes: list[AppliedFileChange] = Field(default_factory=list)
