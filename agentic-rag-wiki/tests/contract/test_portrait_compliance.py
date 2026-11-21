"""FR-MEM-2 画像合规三层：聚合粒度 / 会话 TTL 过期即焚 / ACL 读门（离线策略层+契约不变式）。

不接持久化（真实长期画像落库=M5/企业档放行项）；只断言口径不变式：个人 id 不得入长期、
到期不可读、越权读者被拒。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from src.partitions.memory.service import portrait_policy
from src.platform.types import PortraitAgg, WorkingMemory


# —— 第①层：长期画像只按 role/domain 聚合 ——
def test_aggregation_key_accepts_only_aggregate_dimension() -> None:
    assert portrait_policy.aggregation_key("role", "finance") == "role:finance"
    assert portrait_policy.aggregation_key("domain", "policy") == "domain:policy"
    with pytest.raises(ValueError, match="个人粒度禁入"):
        portrait_policy.aggregation_key("individual", "u123")


def test_portraitagg_rejects_individual_scope() -> None:
    with pytest.raises(ValidationError):  # PortraitScope Literal 不含 individual，类型层即拒
        PortraitAgg(scope="individual", key="individual:u123")


def test_portraitagg_rejects_non_aggregate_key() -> None:
    with pytest.raises(ValidationError, match="禁个人粒度"):
        PortraitAgg(scope="role", key="u123")  # 缺 'role:' 前缀=疑似个人 id，护栏拦下


def test_project_to_long_term_drops_individual_id() -> None:
    """投影到长期：个人 id 丢失（长期无从回溯个人），仅保留聚合键与 ACL。"""
    wm = WorkingMemory(session_id="s1", individual_id="u123", acl_tags=["hr"], content_pointer="ptr/1")
    agg = portrait_policy.project_to_long_term(wm, "role", "recruiter")
    assert agg.key == "role:recruiter"
    assert "u123" not in agg.model_dump_json()  # 个人标识绝不泄漏进长期画像
    assert agg.acl_tags == ["hr"]


# —— 第②层：个人粒度仅会话内、过期即焚 ——
def test_working_memory_requires_positive_ttl() -> None:
    with pytest.raises(ValidationError):
        WorkingMemory(session_id="s1", individual_id="u1", ttl_seconds=0)  # 禁永久留存个人粒度


def test_working_memory_expires_and_is_unreadable() -> None:
    wm = WorkingMemory(session_id="s1", individual_id="u1", created_at=1000.0, ttl_seconds=60)
    assert portrait_policy.read_working_memory(wm, now=1030.0) is wm  # 未到期可读
    assert wm.is_expired(1060.0)
    assert portrait_policy.read_working_memory(wm, now=1060.0) is None  # 过期即焚：不可读


# —— 第③层：画像字段读取走与文档同一 ACL ——
def test_field_read_requires_full_acl_coverage() -> None:
    field_acl = ["hr", "secret"]
    assert portrait_policy.can_read_field(["hr", "secret"], field_acl) is True
    assert portrait_policy.can_read_field(["hr"], field_acl) is False  # 缺 secret=越权，拒
    assert portrait_policy.can_read_field([], field_acl) is False  # 无标签读者被拒


def test_empty_field_acl_is_public_to_any_reader() -> None:
    assert portrait_policy.can_read_field([], []) is True  # 无约束字段=任何持有空集合的读者可读
