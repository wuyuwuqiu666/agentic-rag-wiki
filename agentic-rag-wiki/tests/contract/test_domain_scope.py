"""M4-ca 知识域后端划分契约：把"前端手工选域"换成"后端按 C 区画像划分"这条口径的不变式。

断言：①可见域＝该角色**已聚合画像**携带 acl_tags 的并集（多条同角色取并集，不取交集、更不给全域）；
②画像未建立＝fail-loud（`PortraitNotReady`），冷启动绝不静默放大权限；③个人粒度进不了长期表
（`PortraitAgg` 类型层护栏在存储边界仍拦）；④dev 档存储可覆写（长期表语义，与 append-only 审计分文件，FR-MEM-1）；
⑤划分结果与检索 pre-filter 用同一 ACL 读门口径（`can_read_field`），不另立第二套权限模型；
⑥坏文件不静默当空画像表（NFR-8 fail-loud）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from src.partitions.memory.repo.portrait_store import DevPortraitStore
from src.partitions.memory.service.domain_scope import PortraitNotReady, visible_domains
from src.partitions.memory.service.portrait_policy import aggregation_key, can_read_field
from src.platform.types import PortraitAgg


def _role_portrait(role: str, acl: list[str]) -> PortraitAgg:
    return PortraitAgg(scope="role", key=aggregation_key("role", role), acl_tags=acl, sample_size=1)


def test_visible_domains_is_union_of_that_roles_portraits() -> None:
    portraits = [
        _role_portrait("finance-analyst", ["fin"]),
        _role_portrait("finance-analyst", ["fin", "eng"]),  # 同一角色的第二条聚合
        _role_portrait("hr-partner", ["hr"]),  # 别的角色的画像不得混入
    ]
    assert visible_domains("finance-analyst", portraits) == ["eng", "fin"]


def test_role_without_portrait_grants_nothing_and_fails_loud() -> None:
    with pytest.raises(PortraitNotReady):
        visible_domains("ghost-role", [_role_portrait("finance-analyst", ["fin"])])


@pytest.mark.parametrize("role", ["", "   ", " padded "])
def test_role_identifier_is_validated_before_lookup(role: str) -> None:
    with pytest.raises(ValueError):
        visible_domains(role, [_role_portrait("finance-analyst", ["fin"])])


def test_individual_grain_cannot_enter_the_long_term_store(tmp_path: Path) -> None:
    store = DevPortraitStore(str(tmp_path / "portraits.json"))
    with pytest.raises(ValidationError):  # 键缺 'role:' 前缀=疑似个人 id，类型层护栏在写边界拦下
        store.upsert(PortraitAgg(scope="role", key="u123", acl_tags=["fin"]))


def test_store_overwrites_by_aggregate_key(tmp_path: Path) -> None:
    store = DevPortraitStore(str(tmp_path / "portraits.json"))
    store.upsert(_role_portrait("finance-analyst", ["fin"]))
    store.upsert(_role_portrait("finance-analyst", ["fin", "eng"]))  # 长期画像可衰减可覆写
    assert len(store.all()) == 1
    assert store.get(aggregation_key("role", "finance-analyst")) is not None
    assert visible_domains("finance-analyst", store.all()) == ["eng", "fin"]


def test_resolved_domains_use_the_same_acl_gate_as_retrieval() -> None:
    domains = visible_domains("finance-analyst", [_role_portrait("finance-analyst", ["fin"])])
    assert can_read_field(domains, ["fin"])  # 本域原文可读
    assert not can_read_field(domains, ["hr"])  # 越权域读不到
    assert not can_read_field(domains, ["fin", "hr"])  # 并集口径不变：仍要求持有全部标签


def test_corrupt_portrait_file_fails_loud_not_silently_empty(tmp_path: Path) -> None:
    path = tmp_path / "portraits.json"
    path.write_text('{"portraits": "这不是画像列表"}', encoding="utf-8")
    with pytest.raises((ValueError, ValidationError)):
        DevPortraitStore(str(path)).all()
