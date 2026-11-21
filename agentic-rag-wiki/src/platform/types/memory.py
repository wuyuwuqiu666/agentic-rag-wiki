"""C 区记忆契约：参数提案（FR-MEM-3/4 双级红线）+ 画像合规三层（FR-MEM-2）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

ParamClass = Literal["business", "safety"]  # safety=系统只能出提案，人工批准才生效
ProposalStatus = Literal["proposed", "shadow_verified", "approved", "applied", "rolled_back", "rejected"]
PortraitScope = Literal["role", "domain"]  # FR-MEM-2：长期画像只按角色/知识域聚合，个人粒度禁入


class ParamProposal(BaseModel):
    param: str
    param_class: ParamClass = "business"
    before: float | str | None = None
    after: float | str | None = None
    evidence: list[str] = Field(default_factory=list)  # 依据样本指针（落文件，不入上下文）
    status: ProposalStatus = "proposed"
    shadow_report: str = ""  # 金标回放报告路径（FR-MEM-5 尺子）
    auto_applicable: bool = False  # safety 参数恒 False（机检见 harness/param_class_check.py）

    @model_validator(mode="after")
    def _safety_never_auto(self) -> ParamProposal:
        """跨区三禁之③的类型层护栏：安全参数只能出提案，禁止自动生效。"""
        if self.param_class == "safety" and self.auto_applicable:
            raise ValueError("安全参数不可 auto_applicable（FR-MEM-4 双级红线）")
        return self


class PortraitAgg(BaseModel):
    """FR-MEM-2 长期画像聚合单元：按 role/domain 聚合而非个人档案；字段读取走与文档同一 ACL。

    类型层护栏：scope 仅 role/domain（个人粒度不可入长期）；key 须带 `{{scope}}:` 聚合前缀，
    结构性阻断把个人 id 当聚合键写入长期记忆。指标只存样本指针（正文落文件、不入上下文）。
    """

    scope: PortraitScope
    key: str  # 聚合键，如 role:finance / domain:policy（绝不含个人标识）
    acl_tags: list[str] = Field(default_factory=list)  # 与源文档同一 ACL（读取门，见 portrait_policy.can_read_field）
    sample_size: int = 0
    metric_pointers: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _key_is_aggregate_dimension(self) -> PortraitAgg:
        if not self.key.startswith(f"{self.scope}:"):
            raise ValueError(f"长期画像键须以聚合维度 '{self.scope}:' 打头，禁个人粒度（FR-MEM-2①）")
        return self


class WorkingMemory(BaseModel):
    """FR-MEM-2 个人粒度只存会话内工作记忆：带正 TTL、过期即焚，不得沉淀为长期画像。

    individual_id 仅允许出现在此临时层；ttl_seconds>0 是"必会过期"的类型层前提。
    """

    session_id: str
    individual_id: str  # 个人标识仅限会话内工作记忆（不入长期画像）
    acl_tags: list[str] = Field(default_factory=list)
    content_pointer: str = ""  # 会话内中间态指针（正文落文件）
    created_at: float = 0.0
    ttl_seconds: int = Field(default=3600, gt=0)  # 正 TTL=过期即焚前提（禁永久留存个人粒度）

    def expires_at(self) -> float:
        return self.created_at + self.ttl_seconds

    def is_expired(self, now: float) -> bool:
        return now >= self.expires_at()
