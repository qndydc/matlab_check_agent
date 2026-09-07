"""
Description: 描述 MATLAB 与 Python 实现必须共同满足的外部行为。
References: Pydantic、domain.models。
Referenced By: 契约验证器与差分验证器。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from .models import DomainModel


class ValueContract(DomainModel):
    name: str
    shape: list[int | None] = Field(default_factory=list)
    dtype: str | None = None
    complex_allowed: bool = False
    absolute_tolerance: float = Field(default=1e-8, ge=0)
    relative_tolerance: float = Field(default=1e-5, ge=0)


class ExceptionContract(DomainModel):
    type_name: str
    message_pattern: str | None = None


class FileSideEffect(DomainModel):
    path: str
    operation: Literal["create", "modify", "delete"]


class BehaviorContract(DomainModel):
    unit_id: str
    inputs: list[ValueContract] = Field(default_factory=list)
    outputs: list[ValueContract] = Field(default_factory=list)
    expected_exception: ExceptionContract | None = None
    file_side_effects: list[FileSideEffect] = Field(default_factory=list)
    cases: list[dict[str, Any]] = Field(default_factory=list)
