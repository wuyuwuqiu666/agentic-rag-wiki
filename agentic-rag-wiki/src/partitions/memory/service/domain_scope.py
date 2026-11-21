"""知识域后端划分（FR-MEM-2 画像聚合规则的读侧口径）：主体角色 → 可见知识域。

纯判定、零 IO：可见域只从**已聚合的角色画像**推出（`PortraitAgg.acl_tags` 与文档检索同一 ACL 口径，
见 portrait_policy.can_read_field），无画像即 fail-loud、**绝不默认可见全域**。
把"调用侧手工勾选知识域"换成"后端按画像划分"的同时不放大任何权限；
真实按主体（SSO/LDAP 已鉴权身份）归因画像=鉴权放行档，本模块只执法口径。
"""

from __future__ import annotations

from collections.abc import Iterable

from src.partitions.memory.service.portrait_policy import aggregation_key
from src.platform.types import PortraitAgg


class PortraitNotReady(ValueError):
    """该角色尚无画像聚合记录→后端不划分任何知识域（FR-MEM-2 冷启动禁默认放大权限）。"""


def visible_domains(role: str, portraits: Iterable[PortraitAgg]) -> list[str]:
    """角色的可见知识域＝其 role 聚合画像携带的 acl_tags 并集（排序去重）。

    空角色/含空白=客户端输入问题（ValueError）；角色无画像=PortraitNotReady（不放行，不静默给全域）。
    """
    if not role or role.strip() != role:
        raise ValueError(f"角色标识不合法（空或含首尾空白）：{role!r}")
    key = aggregation_key("role", role)
    found = {tag for p in portraits if p.key == key and p.scope == "role" for tag in p.acl_tags}
    if not found:
        raise PortraitNotReady(f"角色 {role!r} 无画像聚合（键 {key}），后端不划分任何知识域")
    return sorted(found)
