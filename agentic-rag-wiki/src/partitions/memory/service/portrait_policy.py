"""C 区画像合规三层保护（FR-MEM-2）——纯判定，零 IO、零第三方。

三层：①长期画像只按 role/domain 聚合（个人粒度禁入，键护栏见 PortraitAgg 类型层）；
②个人粒度仅会话内 WorkingMemory（TTL>0、过期即焚）；③画像字段读取走与文档同一 ACL（pre-filter 口径）。
真实长期画像落库/PG 持久化=M5/企业档放行项（FR-MEM-6 冷启动期回路只出提案、不落长期画像）；本模块只执法口径。
"""

from __future__ import annotations

from src.platform.types import PortraitAgg, WorkingMemory

_LONGTERM_SCOPES = ("role", "domain")


def aggregation_key(scope: str, name: str) -> str:
    """构造长期画像聚合键：仅接受 role/domain 维度，个人粒度直接拒绝（FR-MEM-2①）。"""
    if scope not in _LONGTERM_SCOPES:
        raise ValueError(f"长期画像只按 {_LONGTERM_SCOPES} 聚合，个人粒度禁入（FR-MEM-2①）：scope={scope}")
    return f"{scope}:{name}"


def can_read_field(reader_acl_tags: list[str], field_acl_tags: list[str]) -> bool:
    """FR-MEM-2③：画像字段读取走与文档同一 ACL——读者须持有字段全部标签（与向量检索 pre-filter 同口径）。"""
    return set(reader_acl_tags).issuperset(field_acl_tags)


def read_working_memory(memory: WorkingMemory, now: float) -> WorkingMemory | None:
    """FR-MEM-2②：工作记忆过期即焚——到期视同已焚，不可读（返回 None）。"""
    return None if memory.is_expired(now) else memory


def project_to_long_term(memory: WorkingMemory, scope: str, name: str) -> PortraitAgg:
    """把会话工作记忆投影为长期聚合画像：只携带聚合键与 ACL，**绝不迁移 individual_id**。

    个人标识在投影中丢失=合规目标（长期侧无从回溯到个人）；scope 非聚合维度则 aggregation_key 抛错。
    """
    return PortraitAgg(
        scope=scope,  # type: ignore[arg-type]
        key=aggregation_key(scope, name),
        acl_tags=list(memory.acl_tags),
        sample_size=1,
        metric_pointers=[memory.content_pointer] if memory.content_pointer else [],
    )
