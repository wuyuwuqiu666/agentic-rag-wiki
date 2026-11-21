"""A 区版本化索引重建（FR-ING-3）：影子索引 + 别名原子切换 + 切换失败自动回滚旧别名。

控制流（安全不变式，可离线复现，不依赖真实 Milvus collection 克隆）：
① 全量重建影子索引（`build_shadow`）——切之前影子不得对外服务；
② `store.alias_swap(active→shadow)` 原子切换别名指针；
③ 切换越界/抛错 → 立即 `store.alias_swap(shadow→active)` 回滚旧别名（"切换失败自动回滚"）；
④ 回滚本身也失败 → 抛 `ProviderError`（NFR-8 fail-loud，绝不容忍别名悬空/静默）。

"切换期间查询错误率 <0.1%" 的数值验收属容量演练（放行项）；此处只锁机制与回滚不变式。
真实别名原子切换由 Milvus 原生 collection alias 兑现（ADR-002），换插不减语义。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from src.platform.providers import VectorStorePort
from src.platform.providers.errors import ProviderError


@dataclass(frozen=True)
class ReindexOutcome:
    """重建结果：applied=是否切到新别名；rolled_back=切换失败后是否已回滚旧别名。"""

    applied: bool
    rolled_back: bool
    active_alias: str
    error: str | None = None


def run_shadow_reindex(
    store: VectorStorePort,
    *,
    active_alias: str,
    shadow_alias: str,
    build_shadow: Callable[[], None],
) -> ReindexOutcome:
    """影子重建→原子切换→失败回滚的编排（纯控制流，副作用只经 store.alias_swap）。"""
    if active_alias == shadow_alias:
        raise ValueError("active_alias 与 shadow_alias 不得同名（无法表达切换）")

    build_shadow()  # ① 先建影子，全量就绪后才允许切
    try:
        store.alias_swap(active_alias, shadow_alias)  # ② 原子切换：别名指针 active→shadow
    except Exception as exc:  # noqa: BLE001 —— 任何切换故障都须触发回滚，不能只挑特定异常
        try:
            store.alias_swap(shadow_alias, active_alias)  # ③ 回滚旧别名
        except Exception as rb_exc:  # noqa: BLE001 —— 回滚也失败=别名悬空，必须 fail-loud
            raise ProviderError(
                f"FR-ING-3 切换失败且回滚旧别名亦失败（别名可能悬空，须人工按 runbook 复位）："
                f"swap={exc!r} rollback={rb_exc!r}"
            ) from rb_exc
        return ReindexOutcome(
            applied=False, rolled_back=True, active_alias=active_alias, error=str(exc)
        )
    return ReindexOutcome(applied=True, rolled_back=False, active_alias=shadow_alias, error=None)
